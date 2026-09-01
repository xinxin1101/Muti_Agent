from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request

from app.api.autonomous import (
    InitialTaskDispatch,
    RequirementDispatchState,
    RequirementRunLaunchResponse,
    RequirementRunLaunchState,
)
from app.api.models import ProductRunRecoveryPreview
from app.api.service import ProductWorkspaceNotReadyError
from app.api.trace import TraceableAutonomousProductRuntimeService
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG
from app.models.operator_recovery import (
    OperatorActionExecutionResult,
    OperatorRecoveryPlan,
)
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.run_recovery import PostgresRunRecoveryStore
from app.runtime.merge_queue import MergeQueueError
from app.runtime.operator_recovery import OperatorActionStaleError, OperatorRecoveryCoordinator
from app.verification.dependency_preflight import DependencyEnvironmentPreflightError
from app.workspace import ProjectProvisionError, WorkspaceGitError

_ACTION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class OperatorAuditResource(Protocol):
    async def dispose(self) -> None: ...


class OperatorAwareAutonomousProductRuntimeService(TraceableAutonomousProductRuntimeService):
    """Product facade with one bounded operator recovery request surface."""

    def __init__(
        self,
        *,
        operator_recovery: OperatorRecoveryCoordinator,
        operator_audit_resource: OperatorAuditResource,
        run_recovery_store: PostgresRunRecoveryStore | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._operator_recovery = operator_recovery
        self._operator_audit_resource = operator_audit_resource
        self._run_recovery_store = run_recovery_store

    async def dispose(self) -> None:
        try:
            await super().dispose()
        finally:
            await self._operator_audit_resource.dispose()
            if self._run_recovery_store is not None:
                await self._run_recovery_store.dispose()

    async def get_run_recovery_preview(self, run_id: UUID) -> ProductRunRecoveryPreview:
        if self._run_recovery_store is None:
            raise PersistenceConflictError("运行恢复检查未配置")
        observation = await self._run_recovery_store.inspect(run_id)
        previous = await self._evidence_store.load_run(run_id)
        persisted_dag = await self._dag_store.load_dag(run_id)
        dag = TaskDAG.model_validate(persisted_dag.dag.model_dump(mode="python"))
        if not dag.tasks or len(dag.tasks) != observation.task_count:
            raise PersistenceConflictError("旧运行的持久化任务图不完整，不能安全恢复")
        project = await self._catalog.get_project(previous.project_id)
        workspace = self._resolve_planning_workspace(project.project_id)
        try:
            current_commit = await asyncio.to_thread(workspace.head_commit)
            baseline_present = await asyncio.to_thread(
                workspace.has_commit, observation.base_commit
            )
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError("无法确认当前仓库工作区") from exc
        if not baseline_present:
            raise PersistenceConflictError("旧运行的基线提交已不在当前仓库，不能安全恢复")
        await self._preflight_workspace(
            workspace,
            verification_commands=tuple(
                command
                for node in dag.tasks
                for command in node.task.verification_commands
            ),
        )
        existing = await self._run_recovery_store.recovered_run_id(run_id)
        remaining = observation.remaining_task_ids
        allocations = sum(
            node.budget_allocation.recommended_token_budget
            for node in dag.tasks
            if node.task.task_id in remaining and node.budget_allocation is not None
        )
        return ProductRunRecoveryPreview(
            run_id=run_id,
            display_status=observation.display_status,
            reason=observation.reason,
            observed_at=observation.observed_at,
            baseline_commit=observation.base_commit,
            current_commit=current_commit,
            baseline_changed=current_commit != observation.base_commit,
            dag_complete=True,
            reusable_task_ids=observation.completed_task_ids,
            checkpointed_task_ids=observation.checkpointed_task_ids,
            remaining_task_ids=remaining,
            estimated_new_budget_tokens=allocations,
            existing_recovery_run_id=existing,
            recovery_available=observation.display_status.value == "RECOVERY_REQUIRED",
            next_action="打开已有恢复运行" if existing else "新建恢复运行",
        )

    async def get_operator_recovery_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        return await self._operator_recovery.get_plan(run_id)

    async def execute_operator_action(
        self,
        *,
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
        return await self._operator_recovery.execute(run_id=run_id, action_id=action_id)

    async def recover_interrupted_run(self, run_id: UUID) -> RequirementRunLaunchResponse:
        """Create one new Run from a diagnosed orphan; never reopen the source Run."""

        if self._run_recovery_store is None:
            raise PersistenceConflictError("运行恢复检查未配置")
        async with self._run_recovery_store.recovery_lock(run_id):
            preview = await self.get_run_recovery_preview(run_id)
            if preview.existing_recovery_run_id is not None:
                return await self._existing_recovery_launch(
                    source_run_id=run_id,
                    recovered_run_id=preview.existing_recovery_run_id,
                )
            if not preview.recovery_available:
                raise PersistenceConflictError("该运行当前不满足新建恢复运行条件")
            previous = await self._evidence_store.load_run(run_id)
            persisted_dag = await self._dag_store.load_dag(run_id)
            full_dag = TaskDAG.model_validate(persisted_dag.dag.model_dump(mode="python"))
            completed = set(preview.reusable_task_ids)
            dag = self._remaining_session_dag(full_dag, completed)
            if dag is None:
                raise PersistenceConflictError("该运行没有未完成工作包可恢复")
            base_commit = preview.current_commit
            session_id = (
                None
                if self._development_session_store is None
                else await self._development_session_store.find_session_id_by_run(run_id)
            )
            # Refresh the resumable session from the immutable source evidence before
            # attaching the new Run.  This preserves checkpoints and bounded context
            # for an existing session without replaying completed work packages.
            if session_id is not None:
                await self._development_session_store.capture_run_progress(
                    session_id=session_id,
                    snapshot=previous,
                )
            if session_id is None and self._development_session_store is not None:
                session_id = await self._development_session_store.create(
                    project_id=previous.project_id,
                    requirement=f"恢复遗留运行 {run_id}",
                    base_commit=previous.base_commit,
                    repository_context_sha256=hashlib.sha256(
                        persisted_dag.dag_sha256.encode("ascii")
                    ).hexdigest(),
                    planning_launch_id=None,
                )
                await self._development_session_store.record_plan(session_id=session_id, dag=dag)
            new_run_id = await self._dag_store.start_run(
                project_id=previous.project_id,
                dag=dag,
                base_commit=base_commit,
            )
            await self._initialize_run_token_budget(new_run_id, dag)
            if session_id is not None:
                await self._attach_development_session_run(
                    session_id,
                    run_id=new_run_id,
                    resumed_from_run_id=run_id,
                )
            elif self._run_recovery_store is not None:
                await self._run_recovery_store.set_resumed_from(
                    run_id=new_run_id,
                    source_run_id=run_id,
                )
            await self._run_recovery_store.link_recovery(
                source_run_id=run_id,
                recovered_run_id=new_run_id,
            )
            persisted_new_dag = await self._dag_store.load_dag(new_run_id)
            initial_ready = tuple(
                dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set())
            )
            if not initial_ready:
                raise PersistenceCorruptionError("persisted TaskDAG has no initial READY task")
            dispatches = tuple(
                await asyncio.gather(
                    *(
                        self._dispatch_initial_task(run_id=new_run_id, task_id=task_id)
                        for task_id in initial_ready
                    )
                )
            )
            queued = sum(item.state is RequirementDispatchState.QUEUED for item in dispatches)
            launch_state = (
                RequirementRunLaunchState.QUEUED
                if queued == len(dispatches)
                else RequirementRunLaunchState.BROKER_UNAVAILABLE
                if queued == 0
                else RequirementRunLaunchState.PARTIAL
            )
            return RequirementRunLaunchResponse(
                run_id=new_run_id,
                project_id=previous.project_id,
                base_commit=base_commit,
                dag_sha256=persisted_new_dag.dag_sha256,
                task_ids=tuple(dag.topological_order()),
                initial_ready_task_ids=initial_ready,
                launch_state=launch_state,
                dispatches=dispatches,
                resumed_from_run_id=run_id,
            )

    async def _existing_recovery_launch(
        self, *, source_run_id: UUID, recovered_run_id: UUID
    ) -> RequirementRunLaunchResponse:
        snapshot = await self._evidence_store.load_run(recovered_run_id)
        persisted = await self._dag_store.load_dag(recovered_run_id)
        ready = tuple(persisted.dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
        return RequirementRunLaunchResponse(
            run_id=recovered_run_id,
            project_id=snapshot.project_id,
            base_commit=snapshot.base_commit,
            dag_sha256=persisted.dag_sha256,
            task_ids=tuple(persisted.dag.topological_order()),
            initial_ready_task_ids=ready,
            launch_state=RequirementRunLaunchState.QUEUED,
            dispatches=tuple(
                InitialTaskDispatch(
                    task_id=task_id,
                    state=RequirementDispatchState.QUEUED,
                    detail="已复用已有恢复运行，未重复创建或分派。",
                )
                for task_id in ready
            ),
            resumed_from_run_id=source_run_id,
            reused_existing_run=True,
        )


def attach_operator_routes(
    app: FastAPI,
    service: OperatorAwareAutonomousProductRuntimeService,
) -> None:
    @app.get(
        "/api/v1/runs/{run_id}/recovery-preview",
        response_model=ProductRunRecoveryPreview,
    )
    async def get_recovery_preview(request: Request, run_id: UUID) -> ProductRunRecoveryPreview:
        if request.query_params:
            raise HTTPException(status_code=400, detail="恢复预览不接受浏览器自定义参数")
        try:
            return await service.get_run_recovery_preview(run_id)
        except ProductWorkspaceNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(status_code=500, detail="旧运行任务图证据损坏，无法恢复") from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ValueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/runs/{run_id}/operator-recovery",
        response_model=OperatorRecoveryPlan,
    )
    async def get_operator_recovery(request: Request, run_id: UUID) -> OperatorRecoveryPlan:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="operator recovery does not accept browser-authored authority selectors",
            )
        try:
            return await service.get_operator_recovery_plan(run_id)
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted operator recovery source facts failed integrity validation",
            ) from exc
        except (ValueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/operator-actions/{action_id}",
        response_model=OperatorActionExecutionResult,
    )
    async def execute_operator_action(
        request: Request,
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
        if request.query_params or await request.body():
            raise HTTPException(
                status_code=400,
                detail="operator action accepts only the server-issued action id",
            )
        if _ACTION_ID_RE.fullmatch(action_id) is None:
            raise HTTPException(status_code=404, detail="operator action does not exist")
        try:
            return await service.execute_operator_action(run_id=run_id, action_id=action_id)
        except OperatorActionStaleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PersistenceConflictError, MergeQueueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TaskDispatchBrokerError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="operator action failed durable integrity revalidation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/recover-as-new",
        response_model=RequirementRunLaunchResponse,
        status_code=201,
    )
    async def recover_interrupted_run(
        request: Request,
        run_id: UUID,
    ) -> RequirementRunLaunchResponse:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "interrupted-run recovery does not accept browser-authored "
                    "task or Git authority"
                ),
            )
        try:
            return await service.recover_interrupted_run(run_id)
        except (
            PersistenceConflictError,
            ProjectProvisionError,
            WorkspaceGitError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="interrupted-run recovery facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
