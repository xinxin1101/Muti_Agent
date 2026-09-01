from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from uuid import UUID, uuid4

from pydantic import SecretStr

from app.api.autonomous import (
    InitialTaskDispatch,
    ProductPlannerUnavailableError,
    RequirementDispatchState,
    RequirementRunCreateRequest,
    RequirementRunLaunchResponse,
    RequirementRunLaunchState,
)
from app.api.lifecycle_rollout import LifecycleRolloutGate
from app.api.models import (
    DispatchStatus,
    ProductProject,
    ProductProjectDeletionPreview,
    ProductProjectDeletionResult,
    ProductRun,
    ProductRunDetail,
    ProjectCreateRequest,
    ProjectDeleteRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.api.operator import OperatorAwareAutonomousProductRuntimeService
from app.api.repository_context import RepositoryPlanningContextBuilder
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG, TaskNode
from app.models.development_session import (
    DevelopmentSessionContinuationMode,
    DevelopmentSessionTimelineKind,
)
from app.models.operation_audit import OperationAuditAction, OperationAuditOutcome
from app.persistence.errors import PersistenceConflictError
from app.persistence.lifecycle import PostgresLifecycleStore, ProjectDeletionTokenSigner
from app.persistence.operation_audit import PostgresOperationAuditStore
from app.persistence.run_recovery import PostgresRunRecoveryStore
from app.persistence.token_budget import TokenBudgetPlanError
from app.workspace import ProjectProvisionError, WorkspaceGitError
from app.workspace.lifecycle import LocalProjectArtifacts


class HardenedOperatorAwareAutonomousProductRuntimeService(
    OperatorAwareAutonomousProductRuntimeService
):
    """Real-world Product entry points over the already-accepted runtime authority layers."""

    def __init__(
        self,
        *,
        planning_context_builder: RepositoryPlanningContextBuilder,
        project_publication_token_recorder: (
            Callable[[UUID, SecretStr | None], Awaitable[None]] | None
        ) = None,
        lifecycle_store: PostgresLifecycleStore,
        operation_audit_store: PostgresOperationAuditStore,
        deletion_token_signer: ProjectDeletionTokenSigner,
        local_project_artifacts: LocalProjectArtifacts,
        run_recovery_store: PostgresRunRecoveryStore | None = None,
        recovery_check_interval_seconds: float = 30.0,
        lifecycle_rollout_gate: LifecycleRolloutGate | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._planning_context_builder = planning_context_builder
        self._project_publication_token_recorder = project_publication_token_recorder
        self._lifecycle_store = lifecycle_store
        self._operation_audit_store = operation_audit_store
        self._deletion_token_signer = deletion_token_signer
        self._local_project_artifacts = local_project_artifacts
        self._run_recovery_store = run_recovery_store
        self._recovery_check_interval_seconds = recovery_check_interval_seconds
        self._recovery_monitor_task: asyncio.Task[None] | None = None
        self._lifecycle_rollout_gate = lifecycle_rollout_gate or LifecycleRolloutGate()

    async def start_recovery_monitor(self) -> None:
        if self._run_recovery_store is not None and self._recovery_monitor_task is None:
            self._recovery_monitor_task = asyncio.create_task(
                self._recovery_monitor_loop(), name="devflow-run-recovery-monitor"
            )

    async def _recovery_monitor_loop(self) -> None:
        assert self._run_recovery_store is not None
        try:
            while True:
                await self._run_recovery_store.refresh_running()
                await asyncio.sleep(self._recovery_check_interval_seconds)
        except asyncio.CancelledError:
            raise

    async def dispose(self) -> None:
        if self._recovery_monitor_task is not None:
            self._recovery_monitor_task.cancel()
            await asyncio.gather(self._recovery_monitor_task, return_exceptions=True)
            self._recovery_monitor_task = None
        try:
            await super().dispose()
        finally:
            await self._lifecycle_store.dispose()

    async def list_projects(self, *, include_archived: bool = False) -> tuple[ProductProject, ...]:
        projects = (
            await self._catalog.list_projects(include_archived=True)
            if include_archived
            else await self._catalog.list_projects()
        )
        return tuple(await asyncio.gather(*(self._strong_project_state(item) for item in projects)))

    async def list_runs(
        self, *, project_id: UUID | None = None, include_archived: bool = False
    ) -> tuple[ProductRun, ...]:
        if self._run_recovery_store is not None:
            await self._run_recovery_store.refresh_running(project_id=project_id)
        return await super().list_runs(
            project_id=project_id,
            include_archived=include_archived,
        )

    async def get_run(self, run_id: UUID) -> ProductRunDetail:
        if self._run_recovery_store is not None:
            await self._run_recovery_store.inspect(run_id)
        return await super().get_run(run_id)

    async def archive_project(self, project_id: UUID) -> ProductProject:
        await self._ensure_lifecycle_rollout_enabled(project_id)
        await self._lifecycle_store.archive_project(project_id)
        await self._record_lifecycle_audit(
            action=OperationAuditAction.PROJECT_ARCHIVED,
            project_id=project_id,
            result_summary="项目已归档；GitHub 仓库和既有运行证据未被修改。",
        )
        if self._development_session_store is not None:
            sessions = await self._development_session_store.list_for_project(project_id, limit=1)
            if sessions:
                await self._development_session_store.append_timeline(
                    session_id=sessions[0].session_id,
                    event_key=f"user-action:PROJECT_ARCHIVED:{project_id}",
                    kind=DevelopmentSessionTimelineKind.USER_ACTION,
                    title="用户确认归档项目",
                    detail="项目已从默认列表隐藏；GitHub 仓库和本地工作区保持不变。",
                    metadata={"action": OperationAuditAction.PROJECT_ARCHIVED.value},
                )
        return await self.get_project(project_id)

    async def restore_project(self, project_id: UUID) -> ProductProject:
        await self._ensure_lifecycle_rollout_enabled(project_id)
        await self._lifecycle_store.restore_project(project_id)
        await self._record_lifecycle_audit(
            action=OperationAuditAction.PROJECT_RESTORED,
            project_id=project_id,
            result_summary="项目已恢复到默认可见列表。",
        )
        return await self.get_project(project_id)

    async def project_deletion_preview(self, project_id: UUID) -> ProductProjectDeletionPreview:
        await self._ensure_lifecycle_rollout_enabled(project_id)
        facts = await self._lifecycle_store.ensure_deletion_allowed(project_id)
        workspace_bytes, cache_bytes = await asyncio.to_thread(
            self._local_project_artifacts.sizes, project_id
        )
        token, expires_at = self._deletion_token_signer.issue(facts)
        return ProductProjectDeletionPreview(
            project_id=project_id,
            required_confirmation_name=facts.required_confirmation_name,
            confirmation_token=token,
            confirmation_expires_at=expires_at,
            run_count=facts.run_count,
            development_session_count=facts.development_session_count,
            local_workspace_bytes=workspace_bytes,
            project_cache_bytes=cache_bytes,
            local_credential_count=facts.local_credential_count,
        )

    async def delete_project(
        self, project_id: UUID, request: ProjectDeleteRequest
    ) -> ProductProjectDeletionResult:
        await self._ensure_lifecycle_rollout_enabled(project_id)
        facts = await self._lifecycle_store.ensure_deletion_allowed(project_id)
        if request.confirmation_name.strip() != facts.required_confirmation_name:
            raise ValueError("确认名称不匹配；未执行任何删除操作")
        self._deletion_token_signer.verify(request.confirmation_token, facts)
        workspace_bytes, cache_bytes = await asyncio.to_thread(
            self._local_project_artifacts.remove, project_id
        )
        await self._lifecycle_store.delete_project(project_id)
        await self._record_lifecycle_audit(
            action=OperationAuditAction.PROJECT_DELETED,
            project_id=project_id,
            result_summary="已删除 DevFlow 本地项目数据、项目工作区与项目专属缓存；GitHub 保留。",
        )
        return ProductProjectDeletionResult(
            project_id=project_id,
            removed_run_count=facts.run_count,
            removed_development_session_count=facts.development_session_count,
            removed_local_workspace_bytes=workspace_bytes,
            removed_project_cache_bytes=cache_bytes,
            removed_local_credential_count=facts.local_credential_count,
        )

    async def archive_run(self, run_id: UUID) -> None:
        snapshot = await self._evidence_store.load_run(run_id)
        await self._ensure_lifecycle_rollout_enabled(snapshot.project_id)
        archived_now = await self._lifecycle_store.archive_run(run_id)
        if not archived_now:
            return
        await self._record_lifecycle_audit(
            action=OperationAuditAction.RUN_ARCHIVED,
            project_id=snapshot.project_id,
            run_id=run_id,
            result_summary="运行已从默认列表归档；执行终态、DAG、事件和证据保持不变。",
        )
        if self._development_session_store is not None:
            session_id = await self._development_session_store.find_session_id_by_run(run_id)
            if session_id is not None:
                await self._development_session_store.append_timeline(
                    session_id=session_id,
                    event_key=f"user-action:RUN_ARCHIVED:{run_id}",
                    kind=DevelopmentSessionTimelineKind.USER_ACTION,
                    title="用户确认归档运行",
                    detail="运行已从默认列表隐藏，执行证据仍可在归档记录中查看。",
                    run_id=run_id,
                    metadata={"action": OperationAuditAction.RUN_ARCHIVED.value},
                )

    async def recover_interrupted_run(self, run_id: UUID) -> RequirementRunLaunchResponse:
        """Keep recovery creation behind the same progressive lifecycle rollout gate."""

        snapshot = await self._evidence_store.load_run(run_id)
        await self._ensure_lifecycle_rollout_enabled(snapshot.project_id)
        return await super().recover_interrupted_run(run_id)

    async def continue_development_session(
        self,
        session_id: UUID,
        *,
        mode: DevelopmentSessionContinuationMode = DevelopmentSessionContinuationMode.AUTO,
    ) -> RequirementRunLaunchResponse:
        session = await self.get_development_session(session_id)
        await self._ensure_lifecycle_rollout_enabled(session.project_id)
        return await super().continue_development_session(session_id, mode=mode)

    async def replan_development_session(self, session_id: UUID) -> RequirementRunLaunchResponse:
        session = await self.get_development_session(session_id)
        await self._ensure_lifecycle_rollout_enabled(session.project_id)
        return await super().replan_development_session(session_id)

    async def _ensure_lifecycle_rollout_enabled(self, project_id: UUID) -> None:
        project = await self.get_project(project_id)
        if not self._lifecycle_rollout_gate.is_enabled(project):
            raise PersistenceConflictError(self._lifecycle_rollout_gate.disabled_reason(project))

    async def _record_lifecycle_audit(
        self,
        *,
        action: OperationAuditAction,
        project_id: UUID,
        result_summary: str,
        run_id: UUID | None = None,
    ) -> None:
        target = run_id or project_id
        await self._operation_audit_store.record(
            operation_key=f"{action.value.lower()}:{target}:{uuid4()}",
            actor="local-product-user",
            action=action,
            outcome=OperationAuditOutcome.SUCCEEDED,
            project_id=project_id,
            run_id=run_id,
            impact_summary={"github_repository_preserved": True},
            result_summary=result_summary,
        )

    async def get_project(self, project_id: UUID) -> ProductProject:
        return await self._strong_project_state(await self._catalog.get_project(project_id))

    async def create_project(self, request: ProjectCreateRequest) -> ProductProject:
        project_id = await self._evidence_store.ensure_project(
            repository_url=str(request.repository_url),
            default_branch=request.default_branch,
        )
        if self._project_publication_token_recorder is not None:
            await self._project_publication_token_recorder(
                project_id,
                request.github_publication_token,
            )
        await self._project_store_call("mark_project_provisioning", project_id)
        try:
            await asyncio.to_thread(
                self._provisioner.provision,
                project_id,
                repository_url=str(request.repository_url),
                default_branch=request.default_branch,
            )
            commit = await asyncio.to_thread(
                self._provisioner.synchronize,  # type: ignore[attr-defined]
                project_id,
                repository_url=str(request.repository_url),
                default_branch=request.default_branch,
            )
            await self._project_store_call(
                "mark_project_ready",
                project_id,
                synced_commit=commit,
            )
        except (ProjectProvisionError, WorkspaceGitError, OSError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__.upper())
            await self._project_store_call(
                "mark_project_failed",
                project_id,
                code=str(code)[:64],
                message=str(exc)[:512] or "Project provisioning failed.",
            )
            raise
        return await self.get_project(project_id)

    async def create_run(self, request: RunCreateRequest) -> RunLaunchResponse:
        await self._lifecycle_store.ensure_project_active(request.project_id)
        project = await self._catalog.get_project(request.project_id)
        base_commit = await self._synchronize_project(project)
        workspace = self._workspace_resolver.resolve(request.project_id)
        dependency_preflight = await self._preflight_workspace(
            workspace,
            verification_commands=tuple(request.task.verification_commands),
        )
        dag = TaskDAG(tasks=(TaskNode(task=request.task, depends_on=()),))
        matches = await self._match_workflows(
            tasks=(request.task,),
            workspace=workspace,
        )
        dag = self._with_workflow_execution_modes(dag, matches)
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        await self._initialize_run_token_budget(run_id, dag)
        await self._record_workflow_matches(
            run_id=run_id,
            matches=matches,
        )
        try:
            receipt = await self._dispatcher.dispatch(
                run_id=run_id,
                task_id=request.task.task_id,
            )
        except TaskDispatchBrokerError as exc:
            return RunLaunchResponse(
                run_id=run_id,
                project_id=project.project_id,
                task_id=request.task.task_id,
                base_commit=base_commit,
                dispatch_status=DispatchStatus.BROKER_UNAVAILABLE,
                detail=str(exc),
                dependency_preflight=dependency_preflight,
            )
        return RunLaunchResponse(
            run_id=run_id,
            project_id=project.project_id,
            task_id=request.task.task_id,
            base_commit=base_commit,
            dispatch_status=DispatchStatus.QUEUED,
            dispatch_id=receipt.dispatch_id,
            broker_message_id=receipt.broker_message_id,
            queue_name=receipt.queue_name,
            dependency_preflight=dependency_preflight,
        )

    async def create_requirement_run(
        self,
        request: RequirementRunCreateRequest,
    ) -> RequirementRunLaunchResponse:
        await self._lifecycle_store.ensure_project_active(request.project_id)
        project = await self._catalog.get_project(request.project_id)
        base_commit = await self._synchronize_project(project)
        try:
            workspace = self._workspace_resolver.resolve(request.project_id)
            dependency_preflight = await self._preflight_workspace(workspace)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProjectProvisionError(
                "frozen repository planning context is unavailable",
                code="PLANNING_CONTEXT_UNAVAILABLE",
            ) from exc

        tracked_files = await asyncio.to_thread(workspace.tracked_files)
        dag = self._requirement_workflow_matcher.match(
            request.requirement,
            repository_files=tracked_files,
        )
        launch_id = None
        planner = None
        development_session_id = None
        if dag is None:
            if self._requirement_planner is None:
                raise ProductPlannerUnavailableError(
                    "natural-language planning is unavailable because the Planner provider is "
                    "not configured"
                )
            try:
                repository_context = await asyncio.to_thread(
                    self._planning_context_builder.build,
                    workspace,
                    base_commit=base_commit,
                    requirement=request.requirement,
                    repository_url=project.repository_url,
                    default_branch=project.default_branch,
                    project_id=request.project_id,
                )
            except (ValueError, WorkspaceGitError) as exc:
                raise ProjectProvisionError(
                    "frozen repository planning context is unavailable",
                    code="PLANNING_CONTEXT_UNAVAILABLE",
                ) from exc
            launch_id = uuid4()
            development_session_id = await self._start_development_session(
                project_id=request.project_id,
                requirement=request.requirement,
                base_commit=base_commit,
                repository_context=repository_context,
                planning_launch_id=launch_id,
            )
            planner = await self._budgeted_requirement_planner(
                launch_id=launch_id,
                project_id=request.project_id,
            )
            try:
                await self._ensure_requirement_planning_capacity(
                    planner,
                    requirement=request.requirement,
                    repository_context=repository_context,
                )
                dag = await planner.plan(
                    request.requirement,
                    repository_context=repository_context,
                )
            except Exception as exc:
                await self._mark_development_session_problem(
                    development_session_id,
                    diagnostic=str(exc),
                    reusable_plan=False,
                )
                raise
        dag = TaskDAG.model_validate(dag.model_dump(mode="python"))
        # Dependency preparation is checked before planning so unavailable registries fail
        # cheaply.  Runtime requirements are only known after the planner emits its immutable
        # task contracts, so validate those capabilities here—still before any Developer Agent
        # is dispatched or consumes a development slice.
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
            if planner is None:
                raise
            await self._mark_development_session_problem(
                development_session_id,
                diagnostic=str(exc),
                reusable_plan=True,
            )
            try:
                dag = await self._replan_requirement_for_budget(
                    planner,
                    requirement=request.requirement,
                    validation_error=str(exc),
                )
            except Exception as replan_exc:
                await self._mark_development_session_problem(
                    development_session_id,
                    diagnostic=str(replan_exc),
                    reusable_plan=True,
                )
                raise
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
        # Store the validated, execution-mode-aware graph.  A continuation must replay the
        # exact accepted plan, not the pre-workflow planning draft.
        try:
            await self._record_development_session_plan(development_session_id, dag)
        except Exception as exc:
            # A generated DAG that cannot be durably saved must not leave a fake PLANNING
            # session behind. The original exception still reaches the API error boundary.
            with suppress(Exception):
                await self._mark_development_session_problem(
                    development_session_id,
                    diagnostic=f"规划结果无法持久化：{exc}",
                    reusable_plan=False,
                )
            raise
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        await self._attach_development_session_run(development_session_id, run_id=run_id)
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

    async def _retry_base_commit(self, project: ProductProject) -> str:
        """Retries use a freshly synchronized managed repository baseline."""

        return await self._synchronize_project(project)

    async def _synchronize_project(self, project: ProductProject) -> str:
        state = await asyncio.to_thread(
            self._provisioner.readiness,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        if not state.ready:
            raise ProjectProvisionError(state.detail, code="WORKSPACE_NOT_READY")
        commit = await asyncio.to_thread(
            self._provisioner.synchronize,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        await self._project_store_call(
            "record_project_sync",
            project.project_id,
            commit=commit,
        )
        return commit

    async def _strong_project_state(self, project: ProductProject) -> ProductProject:
        readiness = await asyncio.to_thread(
            self._provisioner.readiness,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        return project.model_copy(update={"workspace_ready": readiness.ready})

    async def _project_store_call(self, method_name: str, *args, **kwargs) -> None:
        method = getattr(self._evidence_store, method_name, None)
        if method is None:
            raise RuntimeError(f"Project lifecycle store is missing {method_name}")
        await method(*args, **kwargs)
