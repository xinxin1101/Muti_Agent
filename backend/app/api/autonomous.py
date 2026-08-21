from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agents.errors import InvalidPlannerOutputError
from app.api.github_publication import ProductRuntimeServiceWithGitHubPublication
from app.api.service import ProductWorkspaceNotReadyError
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG
from app.models.dispatch import TaskDispatchReceipt
from app.models.integration_gate import HumanGateDecision, IntegrationGateSnapshot
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.providers.errors import AgentProviderError
from app.runtime.durable_human_gate import DurableHumanGateService
from app.workspace import LocalGitWorkspace, WorkspaceGitError

_MAX_REQUIREMENT_CHARS = 12_000
_MAX_CONTEXT_FILES = 400
_MAX_CONTEXT_CHARS = 20_000


class RequirementProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RequirementRunCreateRequest(RequirementProductModel):
    project_id: UUID
    requirement: str = Field(min_length=1, max_length=_MAX_REQUIREMENT_CHARS)

    @field_validator("requirement")
    @classmethod
    def normalize_requirement(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("requirement must not be empty")
        return normalized


class RequirementDispatchState(StrEnum):
    QUEUED = "QUEUED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


class RequirementRunLaunchState(StrEnum):
    QUEUED = "QUEUED"
    PARTIAL = "PARTIAL"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


class InitialTaskDispatch(RequirementProductModel):
    task_id: str = Field(min_length=1, max_length=128)
    state: RequirementDispatchState
    dispatch_id: UUID | None = None
    broker_message_id: str | None = None
    queue_name: str | None = None
    detail: str | None = Field(default=None, max_length=512)


class RequirementRunLaunchResponse(RequirementProductModel):
    run_id: UUID
    project_id: UUID
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    dag_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_ids: tuple[str, ...] = Field(min_length=1)
    initial_ready_task_ids: tuple[str, ...] = Field(min_length=1)
    launch_state: RequirementRunLaunchState
    dispatches: tuple[InitialTaskDispatch, ...] = Field(min_length=1)


class HumanGateDecisionRequest(RequirementProductModel):
    evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: HumanGateDecision
    note: str = Field(default="", max_length=512)

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("human decision note must be a single line")
        return normalized


class RequirementPlanner(Protocol):
    async def plan(
        self,
        requirement: str,
        *,
        repository_context: str | None = None,
    ) -> TaskDAG: ...


class ProductPlannerUnavailableError(RuntimeError):
    """Raised when the natural-language product entry has no configured Planner provider."""


class AutonomousProductRuntimeService(ProductRuntimeServiceWithGitHubPublication):
    """V1 facade plus the natural-language Multi-Agent and durable Human Gate entry points."""

    def __init__(self, *, requirement_planner: RequirementPlanner | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._requirement_planner = requirement_planner
        self._human_gates = DurableHumanGateService(
            evidence_store=self._evidence_store,  # type: ignore[arg-type]
            dag_store=self._dag_store,
            workspace_resolver=self._workspace_resolver,
        )

    async def create_requirement_run(
        self,
        request: RequirementRunCreateRequest,
    ) -> RequirementRunLaunchResponse:
        if self._requirement_planner is None:
            raise ProductPlannerUnavailableError(
                "natural-language planning is unavailable because the Planner provider is not "
                "configured"
            )

        project = await self._catalog.get_project(request.project_id)
        ready = await asyncio.to_thread(self._provisioner.is_ready, request.project_id)
        if not ready:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not ready for project {request.project_id}"
            )

        workspace = self._resolve_planning_workspace(request.project_id)
        try:
            base_commit = await asyncio.to_thread(workspace.head_commit)
            repository_context = await asyncio.to_thread(
                self._build_repository_context,
                workspace,
                repository_url=project.repository_url,
                default_branch=project.default_branch,
                base_commit=base_commit,
            )
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {request.project_id}"
            ) from exc

        dag = await self._requirement_planner.plan(
            request.requirement,
            repository_context=repository_context,
        )
        # Re-validate at the product boundary even though the Planner already validates its output.
        dag = TaskDAG.model_validate(dag.model_dump(mode="python"))
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        persisted_dag = await self._dag_store.load_dag(run_id)

        initial_ready = tuple(
            dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set())
        )
        if not initial_ready:
            raise RuntimeError("validated TaskDAG unexpectedly has no initial READY task")

        dispatches: list[InitialTaskDispatch] = []
        for task_id in initial_ready:
            dispatches.append(await self._dispatch_initial_task(run_id=run_id, task_id=task_id))

        queued = sum(item.state is RequirementDispatchState.QUEUED for item in dispatches)
        if queued == len(dispatches):
            launch_state = RequirementRunLaunchState.QUEUED
        elif queued == 0:
            launch_state = RequirementRunLaunchState.BROKER_UNAVAILABLE
        else:
            launch_state = RequirementRunLaunchState.PARTIAL

        return RequirementRunLaunchResponse(
            run_id=run_id,
            project_id=request.project_id,
            base_commit=base_commit,
            dag_sha256=persisted_dag.dag_sha256,
            task_ids=tuple(dag.topological_order()),
            initial_ready_task_ids=initial_ready,
            launch_state=launch_state,
            dispatches=tuple(dispatches),
        )

    async def list_human_gates(self, run_id: UUID) -> tuple[IntegrationGateSnapshot, ...]:
        return await self._human_gates.list_gates(run_id)

    async def decide_human_gate(
        self,
        *,
        run_id: UUID,
        task_id: str,
        request: HumanGateDecisionRequest,
    ) -> IntegrationGateSnapshot:
        return await self._human_gates.decide(
            run_id=run_id,
            task_id=task_id,
            evidence_fingerprint=request.evidence_fingerprint,
            decision=request.decision,
            note=request.note,
        )

    def _resolve_planning_workspace(self, project_id: UUID) -> LocalGitWorkspace:
        try:
            return self._workspace_resolver.resolve(project_id)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {project_id}"
            ) from exc

    async def _dispatch_initial_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> InitialTaskDispatch:
        try:
            receipt: TaskDispatchReceipt = await self._dispatcher.dispatch(
                run_id=run_id,
                task_id=task_id,
            )
        except TaskDispatchBrokerError as exc:
            return InitialTaskDispatch(
                task_id=task_id,
                state=RequirementDispatchState.BROKER_UNAVAILABLE,
                detail=str(exc)[:512],
            )
        return InitialTaskDispatch(
            task_id=task_id,
            state=RequirementDispatchState.QUEUED,
            dispatch_id=receipt.dispatch_id,
            broker_message_id=receipt.broker_message_id,
            queue_name=receipt.queue_name,
        )

    @staticmethod
    def _build_repository_context(
        workspace: LocalGitWorkspace,
        *,
        repository_url: str,
        default_branch: str,
        base_commit: str,
    ) -> str:
        tracked = workspace.tracked_files()
        visible = tracked[:_MAX_CONTEXT_FILES]
        lines = [
            f"repository_url={repository_url}",
            f"default_branch={default_branch}",
            f"base_commit={base_commit}",
            f"tracked_file_count={len(tracked)}",
            "tracked_files:",
            *visible,
        ]
        if len(tracked) > len(visible):
            lines.append(f"... {len(tracked) - len(visible)} additional tracked files omitted")
        context = "\n".join(lines)
        if len(context) <= _MAX_CONTEXT_CHARS:
            return context
        return context[:_MAX_CONTEXT_CHARS] + "\n... repository context truncated"


def attach_autonomous_routes(
    app: FastAPI,
    service: AutonomousProductRuntimeService,
) -> None:
    """Attach Phase 6 routes without changing the accepted V1 API surface."""

    @app.post(
        "/api/v1/runs/from-requirement",
        response_model=RequirementRunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_requirement_run(
        request: RequirementRunCreateRequest,
    ) -> RequirementRunLaunchResponse:
        try:
            return await service.create_requirement_run(request)
        except ProductPlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProductWorkspaceNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except InvalidPlannerOutputError as exc:
            raise HTTPException(
                status_code=502,
                detail="Planner failed to produce a valid TaskDAG",
            ) from exc
        except AgentProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Planner provider failed: {exc.code.value}",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/runs/{run_id}/human-gates",
        response_model=tuple[IntegrationGateSnapshot, ...],
    )
    async def list_human_gates(run_id: UUID) -> tuple[IntegrationGateSnapshot, ...]:
        try:
            return await service.list_human_gates(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/human-gates/{task_id}/decision",
        response_model=IntegrationGateSnapshot,
    )
    async def decide_human_gate(
        run_id: UUID,
        task_id: str,
        request: HumanGateDecisionRequest,
    ) -> IntegrationGateSnapshot:
        try:
            return await service.decide_human_gate(
                run_id=run_id,
                task_id=task_id,
                request=request,
            )
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except (ValueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
