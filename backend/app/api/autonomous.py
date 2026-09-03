from __future__ import annotations

import asyncio
import hashlib
import logging
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError

from app.agents.errors import InvalidPlannerOutputError
from app.api.failure_explanation import (
    FailureExplanationService,
    FailureExplanationUnavailableError,
)
from app.api.github_publication import ProductRuntimeServiceWithGitHubPublication
from app.api.models import (
    DevelopmentSessionCommandPreviewRequest,
    ProductDependencyPreflight,
    ProductDevelopmentSession,
    ProductDevelopmentSessionCommandPreview,
    ProductDevelopmentSessionRecovery,
    ProductDevelopmentSessionRecoveryBudget,
    ProductDevelopmentSessionTimelineEntry,
    ProductDevelopmentWorkPackage,
    ProductFailureExplanation,
    ProductProject,
)
from app.api.publication import _require_repaired_publication_authority
from app.api.service import (
    ProductDiffUnavailableError,
    ProductWorkspaceNotReadyError,
    _DiffCommitPair,
)
from app.api.session_commands import DevelopmentSessionCommandPreviewer
from app.context.token_estimator import TokenEstimator
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.agent import AgentRole
from app.models.checkpoint import (
    CheckpointReason,
    CheckpointResumeStrategy,
    TaskCheckpoint,
    TaskResumeContext,
)
from app.models.dag import TaskDAG
from app.models.development_session import (
    DevelopmentSessionBaselineState,
    DevelopmentSessionContinuationMode,
    DevelopmentSessionState,
    DevelopmentSessionTimelineKind,
    DevelopmentWorkPackageState,
)
from app.models.dispatch import TaskDispatchReceipt, WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.integration_gate import HumanGateDecision, IntegrationGateSnapshot
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.operation_audit import OperationAuditAction, OperationAuditOutcome
from app.models.token_budget import TokenBudgetStage
from app.persistence.development_session import PostgresDevelopmentSessionStore
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.operation_audit import PostgresOperationAuditStore
from app.persistence.planning_budget import PostgresPlanningTokenBudgetStore
from app.persistence.token_budget import TokenBudgetPlanError
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus, PersistenceEvidenceKind
from app.providers.errors import AgentProviderError, ProviderErrorCode
from app.providers.planning_budgeted import PlanningBudgetedAgentDriver
from app.runtime.durable_human_gate import DurableHumanGateService
from app.runtime.merge_queue import MergeQueueError
from app.verification.dependency_preflight import DependencyEnvironmentPreflightError
from app.workflows.requirement_matcher import RequirementWorkflowMatcher
from app.workspace import LocalGitWorkspace, ProjectProvisionError, WorkspaceGitError

_MAX_REQUIREMENT_CHARS = 12_000
_MAX_CONTEXT_FILES = 400
_MAX_CONTEXT_CHARS = 20_000

logger = logging.getLogger(__name__)


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
    dependency_preflight: ProductDependencyPreflight | None = None
    resumed_from_run_id: UUID | None = None
    reused_existing_run: bool = False


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


class ProductRunController(Protocol):
    async def advance(self, run_id: UUID): ...

    async def dispose(self) -> None: ...


class ProductPlannerUnavailableError(RuntimeError):
    """Raised when the natural-language product entry has no configured Planner provider."""


class AutonomousProductRuntimeService(ProductRuntimeServiceWithGitHubPublication):
    """V1 facade plus natural-language Multi-Agent and durable Human Gate entry points."""

    def __init__(
        self,
        *,
        requirement_planner: RequirementPlanner | None,
        planning_budget_store: PostgresPlanningTokenBudgetStore | None = None,
        development_session_store: PostgresDevelopmentSessionStore | None = None,
        operation_audit_store: PostgresOperationAuditStore | None = None,
        token_estimator: TokenEstimator | None = None,
        failure_explainer: FailureExplanationService | None = None,
        run_controller: ProductRunController | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._requirement_planner = requirement_planner
        self._planning_budget_store = planning_budget_store
        self._development_session_store = development_session_store
        self._operation_audit_store = operation_audit_store
        self._session_command_previewer = DevelopmentSessionCommandPreviewer()
        self._token_estimator = token_estimator or TokenEstimator()
        self._requirement_workflow_matcher = RequirementWorkflowMatcher()
        self._failure_explainer = failure_explainer
        self._run_controller = run_controller
        self._human_gates = DurableHumanGateService(
            evidence_store=self._evidence_store,  # type: ignore[arg-type]
            dag_store=self._dag_store,
            workspace_resolver=self._workspace_resolver,
        )

    async def dispose(self) -> None:
        try:
            if self._run_controller is not None:
                await self._run_controller.dispose()
            if self._failure_explainer is not None:
                await self._failure_explainer.dispose()
            if self._planning_budget_store is not None:
                await self._planning_budget_store.dispose()
            if self._development_session_store is not None:
                await self._development_session_store.dispose()
            if self._operation_audit_store is not None:
                await self._operation_audit_store.dispose()
        finally:
            await super().dispose()

    async def create_requirement_run(
        self,
        request: RequirementRunCreateRequest,
    ) -> RequirementRunLaunchResponse:
        project = await self._catalog.get_project(request.project_id)
        ready = await asyncio.to_thread(self._provisioner.is_ready, request.project_id)
        if not ready:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not ready for project {request.project_id}"
            )

        workspace = self._resolve_planning_workspace(request.project_id)
        try:
            dependency_preflight = await self._preflight_workspace(workspace)
            base_commit = await asyncio.to_thread(workspace.head_commit)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {request.project_id}"
            ) from exc

        tracked_files = await asyncio.to_thread(workspace.tracked_files)
        dag = self._requirement_workflow_matcher.match(
            request.requirement,
            repository_files=tracked_files,
        )
        launch_id: UUID | None = None
        planner: RequirementPlanner | None = None
        repository_context: str | None = None
        if dag is None:
            if self._requirement_planner is None:
                raise ProductPlannerUnavailableError(
                    "natural-language planning is unavailable because the Planner provider is "
                    "not configured"
                )
            try:
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
            launch_id = uuid4()
            planner = await self._budgeted_requirement_planner(
                launch_id=launch_id,
                project_id=request.project_id,
            )
            await self._ensure_requirement_planning_capacity(
                planner,
                requirement=request.requirement,
                repository_context=repository_context,
            )
            dag = await planner.plan(
                request.requirement,
                repository_context=repository_context,
            )
        # Re-validate at the product boundary even though the Planner already validates its output.
        dag = TaskDAG.model_validate(dag.model_dump(mode="python"))
        dependency_preflight = await self._preflight_workspace(
            workspace,
            verification_commands=self._dag_verification_commands(dag),
        )
        matches = await self._match_workflows(
            tasks=tuple(node.task for node in dag.tasks),
            workspace=workspace,
        )
        dag = self._with_workflow_execution_modes(dag, matches)
        try:
            self._validate_run_token_budget_plan(dag)
        except TokenBudgetPlanError as exc:
            # The planning budget permits two bounded calls.  Use the second only when the
            # first valid DAG cannot fund the minimum Agent turns; it is a structural retry,
            # not a blind provider retry.
            if planner is None:
                raise
            dag = await self._replan_requirement_for_budget(
                planner,
                requirement=request.requirement,
                validation_error=str(exc),
            )
            dag = TaskDAG.model_validate(dag.model_dump(mode="python"))
            dependency_preflight = await self._preflight_workspace(
                workspace,
                verification_commands=self._dag_verification_commands(dag),
            )
            matches = await self._match_workflows(
                tasks=tuple(node.task for node in dag.tasks),
                workspace=workspace,
            )
            dag = self._with_workflow_execution_modes(dag, matches)
            self._validate_run_token_budget_plan(dag)
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        await self._initialize_run_token_budget(run_id, dag)
        if launch_id is not None and planner is not None:
            await self._transfer_planner_usage(
                launch_id=launch_id,
                run_id=run_id,
                planner=planner,
            )
        await self._record_workflow_matches(
            run_id=run_id,
            matches=matches,
        )
        persisted_dag = await self._dag_store.load_dag(run_id)

        initial_ready = tuple(dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
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
            dependency_preflight=dependency_preflight,
        )

    async def _budgeted_requirement_planner(
        self,
        *,
        launch_id: UUID,
        project_id: UUID,
    ) -> RequirementPlanner:
        planner = self._requirement_planner
        if planner is None:
            raise ProductPlannerUnavailableError(
                "natural-language planning is unavailable because the Planner provider is "
                "not configured"
            )
        if self._planning_budget_store is None:
            return planner
        await self._planning_budget_store.initialize(
            launch_id=launch_id,
            project_id=project_id,
            enable_thinking=bool(getattr(planner, "enable_thinking", False)),
        )
        clone = getattr(planner, "with_driver", None)
        driver = getattr(planner, "_driver", None)
        if not callable(clone) or driver is None:
            raise RuntimeError("configured Planner does not support DevFlow launch token budgeting")
        return clone(
            PlanningBudgetedAgentDriver(
                driver=driver,
                budget_store=self._planning_budget_store,
                launch_id=launch_id,
                token_estimator=self._token_estimator,
            )
        )

    async def _start_development_session(
        self,
        *,
        project_id: UUID,
        requirement: str,
        base_commit: str,
        repository_context: str,
        planning_launch_id: UUID | None,
    ) -> UUID | None:
        if self._development_session_store is None:
            return None
        return await self._development_session_store.create(
            project_id=project_id,
            requirement=requirement,
            base_commit=base_commit,
            repository_context_sha256=hashlib.sha256(
                repository_context.encode("utf-8")
            ).hexdigest(),
            planning_launch_id=planning_launch_id,
        )

    async def _record_development_session_plan(self, session_id: UUID | None, dag: TaskDAG) -> None:
        if session_id is not None and self._development_session_store is not None:
            await self._development_session_store.record_plan(session_id=session_id, dag=dag)

    async def _mark_development_session_problem(
        self,
        session_id: UUID | None,
        *,
        diagnostic: str,
        reusable_plan: bool,
    ) -> None:
        if session_id is not None and self._development_session_store is not None:
            await self._development_session_store.mark_planning_problem(
                session_id=session_id,
                diagnostic=diagnostic,
                reusable_plan=reusable_plan,
            )

    async def _attach_development_session_run(
        self,
        session_id: UUID | None,
        *,
        run_id: UUID,
        resumed_from_run_id: UUID | None = None,
    ) -> None:
        if session_id is not None and self._development_session_store is not None:
            await self._development_session_store.attach_run(
                session_id=session_id,
                run_id=run_id,
                resumed_from_run_id=resumed_from_run_id,
            )

    @staticmethod
    async def _ensure_requirement_planning_capacity(
        planner: RequirementPlanner,
        *,
        requirement: str,
        repository_context: str,
    ) -> None:
        ensure_capacity = getattr(planner, "ensure_launch_capacity", None)
        if callable(ensure_capacity):
            await ensure_capacity(requirement, repository_context=repository_context)

    @staticmethod
    async def _replan_requirement_for_budget(
        planner: RequirementPlanner,
        *,
        requirement: str,
        validation_error: str,
    ) -> TaskDAG:
        replan = getattr(planner, "replan_for_budget", None)
        if not callable(replan):
            raise TokenBudgetPlanError(
                "规划结果无法满足工作包最低预算，且当前 Planner 不支持紧凑预算重拆分；"
                "未再次发送完整仓库上下文。"
            )
        return await replan(requirement, validation_error=validation_error)

    async def _transfer_planner_usage(
        self,
        *,
        launch_id: UUID,
        run_id: UUID,
        planner: RequirementPlanner,
    ) -> None:
        if self._planning_budget_store is None:
            await self._record_planner_usage(run_id, planner)
            return
        usage = await self._planning_budget_store.link_to_run(
            launch_id=launch_id,
            run_id=run_id,
        )
        if usage.total_tokens and self._token_budget_store is not None:
            await self._token_budget_store.record_usage(
                run_id=run_id,
                role=AgentRole.PLANNER,
                usage=usage,
            )

    async def get_run_metrics(self, run_id: UUID):
        metrics = await super().get_run_metrics(run_id)
        if self._planning_budget_store is None:
            return metrics
        planning_budget = await self._planning_budget_store.snapshot_for_run(run_id)
        return metrics.model_copy(
            update={
                "planning_budget": (
                    None
                    if planning_budget is None
                    else {
                        "total_budget_tokens": planning_budget.total_budget_tokens,
                        "used_total_tokens": planning_budget.used_total_tokens,
                        "attempt_count": planning_budget.attempt_count,
                        "max_attempts": planning_budget.max_attempts,
                        "enable_thinking": planning_budget.enable_thinking,
                        "status": planning_budget.status,
                    }
                )
            }
        )

    async def get_run(self, run_id: UUID):
        detail = await super().get_run(run_id)
        if self._development_session_store is None:
            return detail
        return detail.model_copy(
            update={
                "development_session_id": (
                    await self._development_session_store.find_session_id_by_run(run_id)
                )
            }
        )

    async def retry_run(self, run_id: UUID) -> RequirementRunLaunchResponse:
        """Start a fresh Run from a failed Run's persisted TaskDAG.

        The browser supplies no task, repository, branch, or commit authority. The ended run is
        immutable; this creates a separately auditable run using the server-stored topology.
        """

        previous = await self._evidence_store.load_run(run_id)
        if previous.status is not PersistedRunStatus.FAILED:
            raise PersistenceConflictError("only a failed Run can be started again")
        project = await self._catalog.get_project(previous.project_id)
        base_commit = await self._retry_base_commit(project)
        dependency_preflight = await self._preflight_workspace(
            self._resolve_planning_workspace(previous.project_id)
        )
        persisted_dag = await self._dag_store.load_dag(run_id)
        dag = TaskDAG.model_validate(persisted_dag.dag.model_dump(mode="python"))
        self._validate_run_token_budget_plan(dag)
        dependency_preflight = await self._preflight_workspace(
            self._resolve_planning_workspace(previous.project_id),
            verification_commands=self._dag_verification_commands(dag),
        )
        new_run_id = await self._dag_store.start_run(
            project_id=previous.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        await self._initialize_run_token_budget(new_run_id, dag)
        new_persisted_dag = await self._dag_store.load_dag(new_run_id)
        initial_ready = tuple(dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
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
            dag_sha256=new_persisted_dag.dag_sha256,
            task_ids=tuple(dag.topological_order()),
            initial_ready_task_ids=initial_ready,
            launch_state=launch_state,
            dispatches=dispatches,
            dependency_preflight=dependency_preflight,
        )

    async def get_development_session(self, session_id: UUID) -> ProductDevelopmentSession:
        store = self._require_development_session_store()
        return self._product_development_session(await store.snapshot(session_id))

    async def list_project_development_sessions(
        self, project_id: UUID
    ) -> tuple[ProductDevelopmentSession, ...]:
        store = self._require_development_session_store()
        await self._catalog.get_project(project_id)
        sessions = await store.list_for_project(project_id)
        return tuple(self._product_development_session(session) for session in sessions)

    async def get_development_session_timeline(
        self, session_id: UUID
    ) -> tuple[ProductDevelopmentSessionTimelineEntry, ...]:
        store = self._require_development_session_store()
        session = await store.snapshot(session_id)
        if session.latest_run_id is not None:
            await store.capture_run_progress(
                session_id=session_id,
                snapshot=await self._evidence_store.load_run(session.latest_run_id),
            )
        return tuple(
            ProductDevelopmentSessionTimelineEntry(
                entry_id=entry.entry_id,
                session_id=entry.session_id,
                kind=entry.kind,
                title=entry.title,
                detail=entry.detail,
                run_id=entry.run_id,
                task_id=entry.task_id,
                metadata=entry.metadata,
                created_at=entry.created_at,
            )
            for entry in await store.list_timeline(session_id)
        )

    async def preview_development_session_command(
        self,
        session_id: UUID,
        request: DevelopmentSessionCommandPreviewRequest,
    ) -> ProductDevelopmentSessionCommandPreview:
        """Create a non-authorizing confirmation card for one allow-listed intent."""

        session = await self.get_development_session(session_id)
        project = await self._catalog.get_project(session.project_id)
        return self._session_command_previewer.preview(
            session=session,
            project=project,
            command=request.command,
        )

    async def get_development_session_recovery(
        self, session_id: UUID
    ) -> ProductDevelopmentSessionRecovery:
        store = self._require_development_session_store()
        session = await store.snapshot(session_id)
        if session.latest_run_id is not None:
            await store.capture_run_progress(
                session_id=session_id,
                snapshot=await self._evidence_store.load_run(session.latest_run_id),
            )
            session = await store.snapshot(session_id)
        workspace = self._resolve_planning_workspace(session.project_id)
        current_commit = await asyncio.to_thread(workspace.head_commit)
        preview = await self._development_session_recovery_preview(
            session=session,
            current_commit=current_commit,
        )
        await store.append_timeline(
            session_id=session_id,
            event_key=f"recovery-preview:{session.latest_run_id or 'none'}:{current_commit}",
            kind=DevelopmentSessionTimelineKind.RECOVERY_PREVIEW,
            title="已生成恢复预览",
            detail=(
                f"可复用 {len(preview.reusable_work_package_ids)} 个工作包，"
                f"剩余 {len(preview.remaining_work_package_ids)} 个工作包。"
            ),
            run_id=session.latest_run_id,
            metadata={
                "baseline_state": preview.baseline_state.value,
                "estimated_new_development_tokens": preview.budget.estimated_new_development_tokens,
                "estimated_tokens_saved": preview.budget.estimated_tokens_saved,
            },
        )
        return preview

    async def replan_development_session(self, session_id: UUID) -> RequirementRunLaunchResponse:
        """Start a fresh planning session from the original intent after a baseline change."""

        session = await self._require_development_session_store().snapshot(session_id)
        response = await self.create_requirement_run(
            RequirementRunCreateRequest(
                project_id=session.project_id,
                requirement=session.requirement,
            )
        )
        await self._record_management_audit(
            operation_key=f"development-session-replan:{session_id}:{response.run_id}",
            action=OperationAuditAction.DEVELOPMENT_SESSION_REPLANNED,
            project_id=session.project_id,
            run_id=response.run_id,
            development_session_id=session_id,
            result_summary="已从当前仓库基线创建新的规划会话。",
        )
        return response

    async def continue_development_session(
        self,
        session_id: UUID,
        *,
        mode: DevelopmentSessionContinuationMode = DevelopmentSessionContinuationMode.AUTO,
    ) -> RequirementRunLaunchResponse:
        """Create a new Run from only incomplete work packages; never reopen the source Run."""

        store = self._require_development_session_store()
        async with store.continuation_lock(session_id):
            response = await self._continue_development_session_locked(session_id, mode=mode)
            session = await store.snapshot(session_id)
            await self._record_management_audit(
                operation_key=f"development-session-continue:{session_id}:{response.run_id}",
                action=OperationAuditAction.DEVELOPMENT_SESSION_CONTINUED,
                project_id=session.project_id,
                run_id=response.run_id,
                development_session_id=session_id,
                result_summary="已从未完成工作包创建关联的新运行记录。",
            )
            return response

    async def _record_management_audit(
        self,
        *,
        operation_key: str,
        action: OperationAuditAction,
        project_id: UUID,
        run_id: UUID,
        development_session_id: UUID,
        result_summary: str,
    ) -> None:
        """Persist a non-authorizing, append-only fact after a confirmed action."""

        if self._operation_audit_store is None:
            return
        await self._operation_audit_store.record(
            operation_key=operation_key,
            actor="local-product-user",
            action=action,
            outcome=OperationAuditOutcome.SUCCEEDED,
            project_id=project_id,
            run_id=run_id,
            development_session_id=development_session_id,
            impact_summary={"new_run_id": str(run_id)},
            result_summary=result_summary,
        )
        if self._development_session_store is not None:
            await self._development_session_store.append_timeline(
                session_id=development_session_id,
                event_key=f"user-action:{action.value}:{run_id}",
                kind=DevelopmentSessionTimelineKind.USER_ACTION,
                title=(
                    "用户确认继续开发"
                    if action is OperationAuditAction.DEVELOPMENT_SESSION_CONTINUED
                    else "用户确认重新规划"
                ),
                detail=result_summary,
                run_id=run_id,
                metadata={"action": action.value},
            )

    async def _continue_development_session_locked(
        self,
        session_id: UUID,
        *,
        mode: DevelopmentSessionContinuationMode,
    ) -> RequirementRunLaunchResponse:
        store = self._require_development_session_store()
        session = await store.snapshot(session_id)
        if session.dag is None:
            raise PersistenceConflictError(
                "该开发会话没有可复用的规划草案；请重新规划，不会伪造恢复路径。"
            )
        if session.state not in {
            DevelopmentSessionState.PAUSED_PLANNING,
            DevelopmentSessionState.READY_TO_RUN,
            DevelopmentSessionState.RUNNING,
        }:
            raise PersistenceConflictError("该开发会话当前不能继续执行")
        if session.latest_run_id is not None:
            latest = await self._evidence_store.load_run(session.latest_run_id)
            if latest.status is PersistedRunStatus.RUNNING:
                return await self._existing_development_session_launch(session.latest_run_id)
            await store.capture_run_progress(
                session_id=session_id,
                snapshot=latest,
            )
            session = await store.snapshot(session_id)

        completed = {
            item.task_id
            for item in session.work_packages
            if item.state is DevelopmentWorkPackageState.SUCCEEDED
        }
        remaining = self._remaining_session_dag(session.dag, completed)
        if remaining is None:
            await store.mark_completed(session_id)
            raise PersistenceConflictError(
                "开发会话的所有工作包均已有成功验证证据，无需再次调用 Developer。"
            )

        completed_commits = {
            item.commit_sha
            for item in session.work_packages
            if item.task_id in completed and item.commit_sha is not None
        }
        if len(completed_commits) > 1:
            raise PersistenceConflictError(
                "已完成工作包来自多个未整合提交，无法安全跳过它们；请先完成集成或重新规划。"
            )
        base_commit = next(iter(completed_commits), session.base_commit)
        project = await self._catalog.get_project(session.project_id)
        workspace = self._resolve_planning_workspace(session.project_id)
        current_commit = await asyncio.to_thread(workspace.head_commit)
        if (
            current_commit != session.base_commit
            and mode is not DevelopmentSessionContinuationMode.OLD_BASE
        ):
            raise PersistenceConflictError(
                "仓库基线已变化。请先查看恢复预览，并明确选择基于旧基线继续或重新规划。"
            )
        self._validate_run_token_budget_plan(remaining)
        dependency_preflight = await self._preflight_workspace(
            workspace,
            verification_commands=self._dag_verification_commands(remaining),
        )
        run_id = await self._dag_store.start_run(
            project_id=project.project_id,
            dag=remaining,
            base_commit=base_commit,
        )
        await self._initialize_run_token_budget(run_id, remaining)
        await self._attach_development_session_run(
            session_id,
            run_id=run_id,
            resumed_from_run_id=session.latest_run_id,
        )
        persisted_dag = await self._dag_store.load_dag(run_id)
        ready = tuple(remaining.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
        dispatches = tuple(
            await asyncio.gather(
                *(self._dispatch_initial_task(run_id=run_id, task_id=task_id) for task_id in ready)
            )
        )
        queued = sum(item.state is RequirementDispatchState.QUEUED for item in dispatches)
        return RequirementRunLaunchResponse(
            run_id=run_id,
            project_id=project.project_id,
            base_commit=base_commit,
            dag_sha256=persisted_dag.dag_sha256,
            task_ids=tuple(remaining.topological_order()),
            initial_ready_task_ids=ready,
            launch_state=(
                RequirementRunLaunchState.QUEUED
                if queued == len(dispatches)
                else RequirementRunLaunchState.BROKER_UNAVAILABLE
                if queued == 0
                else RequirementRunLaunchState.PARTIAL
            ),
            dispatches=dispatches,
            dependency_preflight=dependency_preflight,
            resumed_from_run_id=session.latest_run_id,
        )

    async def _existing_development_session_launch(
        self, run_id: UUID
    ) -> RequirementRunLaunchResponse:
        """Return the already-created continuation instead of dispatching a duplicate Run."""

        persisted = await self._dag_store.load_dag(run_id)
        snapshot = await self._evidence_store.load_run(run_id)
        ready = tuple(persisted.dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
        return RequirementRunLaunchResponse(
            run_id=run_id,
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
                    detail="恢复运行已创建，未重复分派。",
                )
                for task_id in ready
            ),
            resumed_from_run_id=run_id,
            reused_existing_run=True,
        )

    async def _development_session_recovery_preview(
        self,
        *,
        session,
        current_commit: str,
    ) -> ProductDevelopmentSessionRecovery:
        completed = tuple(
            item.task_id
            for item in session.work_packages
            if item.state is DevelopmentWorkPackageState.SUCCEEDED
        )
        checkpointed = tuple(
            item.task_id
            for item in session.work_packages
            if item.state is DevelopmentWorkPackageState.CHECKPOINTED
        )
        checkpointed_development_reused = {
            item.task_id
            for item in session.work_packages
            if item.state is DevelopmentWorkPackageState.CHECKPOINTED
            and self._checkpoint_reuses_development(item)
        }
        remaining = (
            ()
            if session.dag is None
            else tuple(
                node.task.task_id
                for node in session.dag.tasks
                if node.task.task_id not in completed
            )
        )
        allocations = (
            {}
            if session.dag is None
            else {
                node.task.task_id: (
                    node.budget_allocation.recommended_token_budget
                    if node.budget_allocation is not None
                    else 0
                )
                for node in session.dag.tasks
            }
        )
        planning_remaining: int | None = None
        if session.planning_launch_id is not None and self._planning_budget_store is not None:
            planning = await self._planning_budget_store.snapshot(session.planning_launch_id)
            planning_remaining = max(
                0,
                planning.total_budget_tokens
                - planning.used_total_tokens
                - planning.reserved_tokens,
            )
        development_remaining: int | None = None
        repair_remaining: int | None = None
        if session.latest_run_id is not None and self._token_budget_store is not None:
            budget = await self._token_budget_store.snapshot(session.latest_run_id)
            by_stage = {item.stage: item for item in budget.stages}
            development = by_stage.get(TokenBudgetStage.DEVELOPMENT)
            repair = by_stage.get(TokenBudgetStage.VERIFICATION_REPAIR)
            if development is not None:
                development_remaining = max(
                    0,
                    development.total_budget_tokens
                    - development.used_tokens
                    - development.reserved_tokens,
                )
            if repair is not None:
                repair_remaining = max(
                    0, repair.total_budget_tokens - repair.used_tokens - repair.reserved_tokens
                )
        baseline_state = (
            DevelopmentSessionBaselineState.UNCHANGED
            if current_commit == session.base_commit
            else DevelopmentSessionBaselineState.CHANGED
        )
        return ProductDevelopmentSessionRecovery(
            session_id=session.session_id,
            source_run_id=session.latest_run_id,
            baseline_commit=session.base_commit,
            current_commit=current_commit,
            baseline_state=baseline_state,
            reusable_work_package_ids=completed,
            checkpointed_work_package_ids=checkpointed,
            remaining_work_package_ids=remaining,
            next_action=(
                "重新规划或明确基于旧基线继续"
                if baseline_state is DevelopmentSessionBaselineState.CHANGED
                else "从检查点继续；优先验证已保存代码"
                if checkpointed_development_reused
                else "继续未完成工作包"
            ),
            budget=ProductDevelopmentSessionRecoveryBudget(
                planning_remaining_tokens=planning_remaining,
                development_remaining_tokens=development_remaining,
                repair_remaining_tokens=repair_remaining,
                estimated_new_development_tokens=sum(
                    allocations.get(item, 0)
                    for item in remaining
                    if item not in checkpointed_development_reused
                ),
                estimated_tokens_saved=sum(
                    allocations.get(item, 0)
                    for item in (*completed, *sorted(checkpointed_development_reused))
                ),
            ),
        )

    @staticmethod
    def _checkpoint_reuses_development(item) -> bool:
        context_state = item.context_state or {}
        context_verification = context_state.get("verification_summary")
        if isinstance(context_verification, str) and context_verification.strip():
            return True
        summary = item.verification_summary.strip()
        return bool(summary) and "尚未执行" not in summary

    def _require_development_session_store(self) -> PostgresDevelopmentSessionStore:
        if self._development_session_store is None:
            raise PersistenceConflictError("开发会话持久化未配置")
        return self._development_session_store

    @staticmethod
    def _remaining_session_dag(dag: TaskDAG, completed: set[str]) -> TaskDAG | None:
        pending = [node for node in dag.tasks if node.task.task_id not in completed]
        if not pending:
            return None
        return TaskDAG(
            tasks=tuple(
                node.model_copy(
                    update={
                        "depends_on": tuple(dep for dep in node.depends_on if dep not in completed)
                    }
                )
                for node in pending
            )
        )

    @staticmethod
    def _completed_task_ids(snapshot: PersistedRunSnapshot) -> set[str]:
        """Read only accepted terminal evidence; never infer completion from a UI state."""

        completed: set[str] = set()
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            execution = WorkerExecutionEvidence.model_validate(evidence.payload)
            if execution.status is WorkerExecutionStatus.SUCCEEDED:
                completed.add(execution.task_id)
        return completed

    @staticmethod
    def _resumable_task_checkpoint(snapshot: PersistedRunSnapshot) -> TaskCheckpoint | None:
        """Return the original checkpoint, including compact continuation state."""

        if snapshot.status is not PersistedRunStatus.FAILED:
            return None
        executions: list[WorkerExecutionEvidence] = []
        for evidence in snapshot.evidence:
            if evidence.kind is PersistenceEvidenceKind.WORKER_EXECUTION:
                executions.append(WorkerExecutionEvidence.model_validate(evidence.payload))
        checkpoints = [item.checkpoint for item in executions if item.checkpoint is not None]
        # A multi-package Run can have completed upstream work plus one failed
        # checkpointed package.  Completed packages must be reused, not make that
        # checkpoint ineligible for a new Run.
        if len(checkpoints) != 1:
            return None
        checkpoint = checkpoints[0]
        assert checkpoint is not None
        if checkpoint.base_commit != snapshot.base_commit:
            raise PersistenceCorruptionError(
                "checkpoint does not descend from the persisted Run base"
            )
        return checkpoint

    @staticmethod
    def _checkpoint_resume_strategy(checkpoint: TaskCheckpoint) -> CheckpointResumeStrategy:
        """Resolve explicit strategy and infer safe behavior for historical checkpoints."""

        if checkpoint.resume_strategy is not None:
            return checkpoint.resume_strategy
        if checkpoint.reason is CheckpointReason.VERIFICATION_FAILURE:
            return CheckpointResumeStrategy.VERIFY_THEN_REPAIR

        context_state = checkpoint.context_state
        if context_state is not None and context_state.verification_summary.strip():
            return CheckpointResumeStrategy.VERIFY_THEN_REPAIR
        if (
            checkpoint.verification_summary.strip()
            and "尚未执行" not in checkpoint.verification_summary
        ):
            return CheckpointResumeStrategy.VERIFY_THEN_REPAIR
        return CheckpointResumeStrategy.CONTINUE_DEVELOPMENT

    @staticmethod
    def _with_checkpoint_resume_context(
        *,
        dag: TaskDAG,
        checkpoint: TaskCheckpoint,
        source_run_id: UUID,
    ) -> TaskDAG:
        """Bind one recovered task to bounded checkpoint facts on a new server DAG."""

        strategy = AutonomousProductRuntimeService._checkpoint_resume_strategy(checkpoint)
        context = TaskResumeContext(
            source_run_id=source_run_id,
            checkpoint_commit_sha=checkpoint.commit_sha,
            strategy=strategy,
            context_state=checkpoint.context_state,
            verification_summary=checkpoint.verification_summary,
            failure_summary=checkpoint.failure_summary,
            remaining_summary=checkpoint.remaining_summary,
        )
        if checkpoint.task_id not in dag.task_ids:
            raise PersistenceCorruptionError("checkpoint task is absent from resumed DAG")
        return TaskDAG(
            tasks=tuple(
                node.model_copy(update={"resume_context": context})
                if node.task.task_id == checkpoint.task_id
                else node
                for node in dag.tasks
            )
        )

    @staticmethod
    def _product_development_session(session) -> ProductDevelopmentSession:
        return ProductDevelopmentSession(
            session_id=session.session_id,
            project_id=session.project_id,
            requirement=session.requirement,
            base_commit=session.base_commit,
            state=session.state,
            planning_diagnostic=session.planning_diagnostic,
            latest_run_id=session.latest_run_id,
            resumed_from_run_id=session.resumed_from_run_id,
            work_packages=tuple(
                ProductDevelopmentWorkPackage(
                    task_id=item.task_id,
                    state=item.state,
                    source_run_id=item.source_run_id,
                    commit_sha=item.commit_sha,
                    completed_interfaces=item.completed_interfaces,
                    verification_summary=item.verification_summary,
                    failure_summary=item.failure_summary,
                    remaining_budget_tokens=item.remaining_budget_tokens,
                )
                for item in session.work_packages
            ),
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    async def resume_run(self, run_id: UUID) -> RequirementRunLaunchResponse:
        """Create a new, auditable Run from the sole fenced checkpoint of a failed Run."""

        previous = await self._evidence_store.load_run(run_id)
        if previous.status is not PersistedRunStatus.FAILED:
            raise PersistenceConflictError("only a failed Run can continue from a checkpoint")
        checkpoint = self._resumable_task_checkpoint(previous)
        if checkpoint is None:
            raise PersistenceConflictError(
                "this failed Run has no unambiguous continuation checkpoint"
            )
        project = await self._catalog.get_project(previous.project_id)
        # Synchronize first to validate the managed repository identity, but intentionally retain
        # the fenced checkpoint commit as the new Run's immutable base.
        await self._retry_base_commit(project)
        dependency_preflight = await self._preflight_workspace(
            self._resolve_planning_workspace(previous.project_id)
        )
        persisted_dag = await self._dag_store.load_dag(run_id)
        full_dag = TaskDAG.model_validate(persisted_dag.dag.model_dump(mode="python"))
        dag = self._remaining_session_dag(full_dag, self._completed_task_ids(previous))
        if dag is None:
            raise PersistenceConflictError("this failed Run has no remaining task to continue")
        dag = self._with_checkpoint_resume_context(
            dag=dag,
            checkpoint=checkpoint,
            source_run_id=run_id,
        )
        self._validate_run_token_budget_plan(dag)
        dependency_preflight = await self._preflight_workspace(
            self._resolve_planning_workspace(previous.project_id),
            verification_commands=self._dag_verification_commands(dag),
        )
        new_run_id = await self._dag_store.start_run(
            project_id=previous.project_id,
            dag=dag,
            base_commit=checkpoint.commit_sha,
        )
        await self._initialize_run_token_budget(new_run_id, dag)
        attached_to_session = False
        if self._development_session_store is not None:
            session_id = await self._development_session_store.find_session_id_by_run(run_id)
            if session_id is not None:
                await self._development_session_store.capture_run_progress(
                    session_id=session_id,
                    snapshot=previous,
                )
                await self._attach_development_session_run(
                    session_id,
                    run_id=new_run_id,
                    resumed_from_run_id=run_id,
                )
                attached_to_session = True
        if not attached_to_session and (
            run_recovery_store := getattr(self, "_run_recovery_store", None)
        ) is not None:
            await run_recovery_store.set_resumed_from(
                run_id=new_run_id,
                source_run_id=run_id,
            )
        new_persisted_dag = await self._dag_store.load_dag(new_run_id)
        initial_ready = tuple(dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
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
            base_commit=checkpoint.commit_sha,
            dag_sha256=new_persisted_dag.dag_sha256,
            task_ids=tuple(dag.topological_order()),
            initial_ready_task_ids=initial_ready,
            launch_state=launch_state,
            dispatches=dispatches,
            dependency_preflight=dependency_preflight,
            resumed_from_run_id=run_id,
        )

    async def explain_run_failure(self, run_id: UUID) -> ProductFailureExplanation:
        snapshot = await self._evidence_store.load_run(run_id)
        if snapshot.status is not PersistedRunStatus.FAILED:
            raise PersistenceConflictError("AI 解读只适用于已失败的运行")
        if self._failure_explainer is None:
            raise FailureExplanationUnavailableError(
                "AI 解读未配置模型服务，请先配置 SiliconFlow 密钥。"
            )
        return await self._failure_explainer.explain(
            run_id=run_id,
            failures=self._failure_summaries(snapshot),
        )

    async def _retry_base_commit(self, project: ProductProject) -> str:
        ready = await asyncio.to_thread(self._provisioner.is_ready, project.project_id)
        if not ready:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not ready for project {project.project_id}"
            )
        workspace = self._resolve_planning_workspace(project.project_id)
        try:
            return await asyncio.to_thread(workspace.head_commit)
        except WorkspaceGitError as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {project.project_id}"
            ) from exc

    async def list_human_gates(self, run_id: UUID) -> tuple[IntegrationGateSnapshot, ...]:
        return await self._human_gates.list_gates(run_id)

    async def decide_human_gate(
        self,
        *,
        run_id: UUID,
        task_id: str,
        request: HumanGateDecisionRequest,
    ) -> IntegrationGateSnapshot:
        decided = await self._human_gates.decide(
            run_id=run_id,
            task_id=task_id,
            evidence_fingerprint=request.evidence_fingerprint,
            decision=request.decision,
            note=request.note,
        )
        # A human decision is only an authorization/state transition. Re-enter the controller from
        # durable facts so ABORT can finalize safely and AUTHORIZE_REPAIR can reach only a later
        # bounded repair stage. The browser never supplies scheduler/Git/lease authority here.
        if self._run_controller is not None:
            await self._run_controller.advance(run_id)
        return decided

    def _resolve_planning_workspace(self, project_id: UUID) -> LocalGitWorkspace:
        try:
            return self._workspace_resolver.resolve(project_id)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {project_id}"
            ) from exc

    @staticmethod
    def _integration_commit_pair(
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> _DiffCommitPair:
        candidates: list[_DiffCommitPair] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT:
                continue
            try:
                merge_snapshot = MergeQueueSnapshot.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted merge queue snapshot failed schema validation"
                ) from exc
            if merge_snapshot.run_base_commit != snapshot.base_commit:
                raise PersistenceCorruptionError(
                    "persisted merge queue snapshot does not match the Run base commit"
                )
            for attempt in merge_snapshot.attempts:
                if attempt.task_id != task_id or attempt.outcome not in {
                    MergeAttemptOutcome.INTEGRATED,
                    MergeAttemptOutcome.REPAIRED,
                }:
                    continue
                if attempt.integration_commit is None:
                    raise PersistenceCorruptionError(
                        "successful merge attempt lacks an integration commit"
                    )
                if attempt.outcome is MergeAttemptOutcome.REPAIRED:
                    _require_repaired_publication_authority(snapshot, attempt)
                candidates.append(
                    _DiffCommitPair(
                        base_commit=attempt.previous_integration_commit,
                        head_commit=attempt.integration_commit,
                        evidence_id=evidence.id,
                        evidence_sha256=evidence.payload_sha256,
                        task_commit=attempt.task_commit,
                        task_base_commit=attempt.task_base_commit,
                    )
                )
        if not candidates:
            raise ProductDiffUnavailableError(
                f"integration diff is not available for task {task_id!r}"
            )
        unique = {
            (
                item.base_commit,
                item.head_commit,
                item.task_commit,
                item.task_base_commit,
            )
            for item in candidates
        }
        if len(unique) != 1:
            raise PersistenceCorruptionError(
                "persisted merge queue evidence defines conflicting integration commit pairs"
            )
        return max(candidates, key=lambda item: item.evidence_id)

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
    def _dag_verification_commands(dag: TaskDAG) -> tuple[str, ...]:
        """Return the immutable command contract that must be environment-checked pre-dispatch."""

        return tuple(command for node in dag.tasks for command in node.task.verification_commands)

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
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except InvalidPlannerOutputError as exc:
            raise HTTPException(
                status_code=422,
                detail="规划不可执行：工作包计划未通过结构校验，未启动 Developer。",
            ) from exc
        except TokenBudgetPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AgentProviderError as exc:
            # ProviderErrorCode values are wire-level lowercase strings (for example,
            # ``token_budget_exhausted``). Compare the enum itself so budget exhaustion is
            # never misreported as a generic 502 provider failure.
            if exc.code is ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED:
                raise HTTPException(
                    status_code=429,
                    detail=(f"本次启动的规划模型预算已用尽，未向模型服务发起超额请求。原因：{exc}"),
                ) from exc
            raise HTTPException(
                status_code=502,
                detail=f"Planner provider failed: {exc.code.value}",
            ) from exc
        except (PersistenceConflictError, ProjectProvisionError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SQLAlchemyError as exc:
            # Convert persistence faults into a normal FastAPI response so CORSMiddleware
            # can retain the browser's access-control headers. The detailed traceback remains
            # in the backend log; the client receives no database internals or credentials.
            logger.exception("failed to persist requirement-run creation state")
            raise HTTPException(
                status_code=500,
                detail="创建开发会话的本地持久化失败，请查看后端日志。",
            ) from exc
        except ValueError as exc:
            detail = str(exc)
            # Only an explicit missing project is a 404. Validation and implementation
            # errors must not be misrepresented as a nonexistent resource in the UI.
            status_code = 404 if detail.startswith("unknown ") else 422
            raise HTTPException(status_code=status_code, detail=detail) from exc

    @app.get(
        "/api/v1/projects/{project_id}/development-sessions",
        response_model=tuple[ProductDevelopmentSession, ...],
    )
    async def list_project_development_sessions(
        project_id: UUID,
    ) -> tuple[ProductDevelopmentSession, ...]:
        try:
            return await service.list_project_development_sessions(project_id)
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/development-sessions/{session_id}",
        response_model=ProductDevelopmentSession,
    )
    async def get_development_session(session_id: UUID) -> ProductDevelopmentSession:
        try:
            return await service.get_development_session(session_id)
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/development-sessions/{session_id}/timeline",
        response_model=tuple[ProductDevelopmentSessionTimelineEntry, ...],
    )
    async def get_development_session_timeline(
        session_id: UUID,
    ) -> tuple[ProductDevelopmentSessionTimelineEntry, ...]:
        try:
            return await service.get_development_session_timeline(session_id)
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/development-sessions/{session_id}/command-preview",
        response_model=ProductDevelopmentSessionCommandPreview,
    )
    async def preview_development_session_command(
        session_id: UUID,
        request: DevelopmentSessionCommandPreviewRequest,
    ) -> ProductDevelopmentSessionCommandPreview:
        try:
            return await service.preview_development_session_command(session_id, request)
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/development-sessions/{session_id}/recovery-preview",
        response_model=ProductDevelopmentSessionRecovery,
    )
    async def get_development_session_recovery(
        session_id: UUID,
    ) -> ProductDevelopmentSessionRecovery:
        try:
            return await service.get_development_session_recovery(session_id)
        except (PersistenceConflictError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/development-sessions/{session_id}/continue",
        response_model=RequirementRunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def continue_development_session(
        request: Request,
        session_id: UUID,
        mode: DevelopmentSessionContinuationMode = DevelopmentSessionContinuationMode.AUTO,
    ) -> RequirementRunLaunchResponse:
        if (await request.body()).strip():
            raise HTTPException(status_code=400, detail="继续开发不接受浏览器提供的任务或 Git 参数")
        try:
            return await service.continue_development_session(session_id, mode=mode)
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except TokenBudgetPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (PersistenceConflictError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/development-sessions/{session_id}/replan",
        response_model=RequirementRunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def replan_development_session(
        request: Request, session_id: UUID
    ) -> RequirementRunLaunchResponse:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(status_code=400, detail="重新规划不接受浏览器提供的任务或 Git 参数")
        try:
            return await service.replan_development_session(session_id)
        except ProductPlannerUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except TokenBudgetPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AgentProviderError as exc:
            raise HTTPException(
                status_code=502, detail=f"Planner provider failed: {exc.code.value}"
            ) from exc
        except (PersistenceConflictError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/retry",
        response_model=RequirementRunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def retry_run(request: Request, run_id: UUID) -> RequirementRunLaunchResponse:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail="Run retry does not accept browser-authored task or Git authority",
            )
        try:
            return await service.retry_run(run_id)
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except TokenBudgetPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (
            PersistenceConflictError,
            ProductWorkspaceNotReadyError,
            ProjectProvisionError,
            WorkspaceGitError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted Run retry source facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/resume",
        response_model=RequirementRunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def resume_run(request: Request, run_id: UUID) -> RequirementRunLaunchResponse:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail="Run continuation does not accept browser-authored task or Git authority",
            )
        try:
            return await service.resume_run(run_id)
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except TokenBudgetPlanError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (
            PersistenceConflictError,
            ProductWorkspaceNotReadyError,
            ProjectProvisionError,
            WorkspaceGitError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500, detail="persisted checkpoint failed integrity validation"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/failure-explanation",
        response_model=ProductFailureExplanation,
    )
    async def explain_run_failure(request: Request, run_id: UUID) -> ProductFailureExplanation:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail="AI failure explanation accepts no browser-authored diagnostic data",
            )
        try:
            return await service.explain_run_failure(run_id)
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted failure evidence failed integrity validation",
            ) from exc
        except FailureExplanationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
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
        except (PersistenceConflictError, MergeQueueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except (ValueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
