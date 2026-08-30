from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.api.metrics import (
    MAX_METRIC_EVENT_SCAN,
    METRIC_EVENT_PAGE_SIZE,
    aggregate_runtime_events,
    build_run_metrics,
)
from app.api.models import (
    DispatchStatus,
    ProductDAGEdge,
    ProductDAGNode,
    ProductDAGNodeState,
    ProductDAGStateBasis,
    ProductDependencyCacheCleanup,
    ProductDependencyEnvironmentMetrics,
    ProductDependencyEnvironmentStatus,
    ProductDependencyPreflight,
    ProductDiffEvidenceBasis,
    ProductDiffFile,
    ProductDiffFileStatus,
    ProductDiffKind,
    ProductDiffOmissionReason,
    ProductEvidenceSummary,
    ProductProject,
    ProductRun,
    ProductRunCheckpoint,
    ProductRunDAG,
    ProductRunDetail,
    ProductRunFailure,
    ProductRunMetrics,
    ProductTaskDetail,
    ProductTaskDiff,
    ProductTaskSummary,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.agent import AgentRole, TokenUsage
from app.models.dag import TaskDAG, TaskNode
from app.models.dispatch import (
    TaskDispatchReceipt,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.events import PersistedRuntimeEvent
from app.models.failure import FailureReport
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.run import RunEvent, TaskRunState
from app.models.task import TaskContract
from app.models.verification import VerificationResult
from app.models.work_package import WorkPackageActivationMode
from app.models.workflow import (
    WorkflowActivationMode,
    WorkflowExecutionMode,
    WorkflowExecutionRecord,
    WorkflowMatch,
)
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.interface_contracts import PostgresInterfaceContractRegistry
from app.persistence.serialization import decode_evidence
from app.persistence.token_budget import PostgresRunTokenBudgetStore
from app.persistence.types import (
    PersistedRunSnapshot,
    PersistenceEvidenceKind,
)
from app.verification import DeterministicVerifier
from app.verification.dependency_preflight import DependencyEnvironmentPreflight
from app.workflows import WorkflowMatcher, WorkflowRegistry
from app.workspace import (
    CommitDiffError,
    LocalGitWorkspace,
    ReadOnlyCommitDiffReader,
    WorkspaceGitError,
)


class ProductWorkspaceNotReadyError(RuntimeError):
    """Raised when a persisted Project has no trustworthy managed Git workspace."""


class ProductDependencyEnvironmentUnavailableError(RuntimeError):
    """Raised when runtime composition did not configure dependency environment controls."""


class ProductDiffUnavailableError(RuntimeError):
    """Raised when accepted Git evidence does not yet define the requested diff."""


class ProductMetricsUnavailableError(RuntimeError):
    """Raised when a complete bounded Run Metrics projection cannot be produced."""


class ProductCatalog(Protocol):
    async def list_projects(self, *, limit: int = 100) -> tuple[ProductProject, ...]: ...
    async def get_project(self, project_id: UUID) -> ProductProject: ...
    async def list_runs(
        self,
        *,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[ProductRun, ...]: ...
    async def dispose(self) -> None: ...


class ProductEvidenceStore(Protocol):
    async def ensure_project(
        self,
        *,
        repository_url: str,
        default_branch: str,
        project_id: UUID | None = None,
    ) -> UUID: ...
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...
    async def list_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]: ...
    async def append_evidence(
        self,
        *,
        run_id: UUID,
        evidence_key: str,
        kind: PersistenceEvidenceKind,
        payload_model: BaseModel,
        task_id: str | None = None,
        stage: str | None = None,
    ) -> int: ...
    async def dispose(self) -> None: ...


class ProductDAGStore(Protocol):
    async def start_run(
        self,
        *,
        project_id: UUID,
        dag: TaskDAG,
        base_commit: str,
        run_id: UUID | None = None,
    ) -> UUID: ...
    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot: ...
    async def dispose(self) -> None: ...


class ProductProvisioner(Protocol):
    def provision(self, project_id: UUID, *, repository_url: str, default_branch: str) -> None: ...
    def is_ready(self, project_id: UUID) -> bool: ...


class ProductWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class ProductDispatcher(Protocol):
    async def dispatch(self, *, run_id: UUID, task_id: str) -> TaskDispatchReceipt: ...


@dataclass(frozen=True)
class _DiffCommitPair:
    base_commit: str
    head_commit: str
    evidence_id: int
    evidence_sha256: str
    task_commit: str | None = None
    task_base_commit: str | None = None


class ProductRuntimeService:
    """Browser-facing product facade that delegates execution to accepted runtime boundaries."""

    def __init__(
        self,
        *,
        catalog: ProductCatalog,
        evidence_store: ProductEvidenceStore,
        dag_store: ProductDAGStore,
        provisioner: ProductProvisioner,
        workspace_resolver: ProductWorkspaceResolver,
        dispatcher: ProductDispatcher,
        dependency_preflight: DependencyEnvironmentPreflight | None = None,
        token_budget_store: PostgresRunTokenBudgetStore | None = None,
        interface_contract_registry: PostgresInterfaceContractRegistry | None = None,
        developer_max_output_tokens: int = 1_400,
        workflow_activation_mode: WorkflowActivationMode = WorkflowActivationMode.WORKFLOW_FIRST,
        work_package_activation_mode: WorkPackageActivationMode = (
            WorkPackageActivationMode.WORK_PACKAGE_FIRST
        ),
    ) -> None:
        self._catalog = catalog
        self._evidence_store = evidence_store
        self._dag_store = dag_store
        self._provisioner = provisioner
        self._workspace_resolver = workspace_resolver
        self._dispatcher = dispatcher
        self._dependency_preflight = dependency_preflight
        self._token_budget_store = token_budget_store
        self._interface_contract_registry = interface_contract_registry
        self._developer_max_output_tokens = developer_max_output_tokens
        self._workflow_activation_mode = workflow_activation_mode
        self._work_package_activation_mode = work_package_activation_mode
        self._workflow_matcher = WorkflowMatcher(WorkflowRegistry.default())

    async def dispose(self) -> None:
        await self._catalog.dispose()
        await self._evidence_store.dispose()
        await self._dag_store.dispose()
        if self._token_budget_store is not None:
            await self._token_budget_store.dispose()
        if self._interface_contract_registry is not None:
            await self._interface_contract_registry.dispose()

    async def _initialize_run_token_budget(self, run_id: UUID, dag: TaskDAG | None = None) -> None:
        """Persist the budget before dispatch so queued runs have an observable budget."""

        if self._token_budget_store is not None:
            await self._token_budget_store.initialize(run_id)
            if (
                dag is not None
                and self._work_package_activation_mode is not WorkPackageActivationMode.LEGACY_DAG
            ):
                await self._token_budget_store.initialize_hierarchy(
                    run_id=run_id,
                    dag=dag,
                    developer_max_output_tokens=self._developer_max_output_tokens,
                )
        if (
            self._interface_contract_registry is not None
            and dag is not None
            and self._work_package_activation_mode is WorkPackageActivationMode.CONTRACT_GATED
        ):
            await self._interface_contract_registry.declare_for_dag(run_id=run_id, dag=dag)

    def _validate_run_token_budget_plan(self, dag: TaskDAG) -> None:
        """Fail before persistence/dispatch when package minimums cannot be funded."""

        if (
            self._token_budget_store is not None
            and self._work_package_activation_mode is not WorkPackageActivationMode.LEGACY_DAG
        ):
            self._token_budget_store.validate_hierarchy_plan(
                dag=dag,
                developer_max_output_tokens=self._developer_max_output_tokens,
            )

    async def _record_planner_usage(self, run_id: UUID, planner: object) -> None:
        if self._token_budget_store is None:
            return
        usage = getattr(planner, "last_usage", None)
        if isinstance(usage, TokenUsage) and usage.total_tokens:
            await self._token_budget_store.record_usage(
                run_id=run_id,
                role=AgentRole.PLANNER,
                usage=usage,
            )

    async def _match_workflows(
        self,
        *,
        tasks: tuple[TaskContract, ...],
        workspace: LocalGitWorkspace,
    ) -> tuple[WorkflowMatch, ...]:
        """Select the pure-rule runtime route before the immutable DAG is persisted."""

        tracked_files = getattr(workspace, "tracked_files", None)
        repository_files = await asyncio.to_thread(tracked_files) if callable(tracked_files) else ()
        matches = self._workflow_matcher.match_tasks(
            tasks,
            repository_files=repository_files,
        )
        return tuple(self._apply_workflow_activation(match) for match in matches)

    def _apply_workflow_activation(self, match: WorkflowMatch) -> WorkflowMatch:
        if self._workflow_activation_mode is WorkflowActivationMode.AGENT_ONLY:
            return match.model_copy(update={"execution_mode": WorkflowExecutionMode.AGENT})
        return match

    async def _record_workflow_matches(
        self,
        *,
        run_id: UUID,
        matches: tuple[WorkflowMatch, ...],
    ) -> None:
        """Persist deterministic match evidence after the Run identity exists."""

        append_evidence = getattr(self._evidence_store, "append_evidence", None)
        if not callable(append_evidence):
            return
        for match in matches:
            await append_evidence(
                run_id=run_id,
                evidence_key=f"workflow-match:{match.task_id}",
                kind=PersistenceEvidenceKind.WORKFLOW_MATCH,
                payload_model=match,
                task_id=match.task_id,
                stage="workflow-match",
            )

    @staticmethod
    def _with_workflow_execution_modes(
        dag: TaskDAG,
        matches: tuple[WorkflowMatch, ...],
    ) -> TaskDAG:
        modes = {match.task_id: match.execution_mode for match in matches}
        if set(modes) != set(dag.task_ids):
            raise ValueError("workflow matches must cover every persisted DAG task")
        return TaskDAG(
            tasks=tuple(
                node.model_copy(update={"execution_mode": modes[node.task.task_id]})
                for node in dag.tasks
            )
        )

    async def list_projects(self) -> tuple[ProductProject, ...]:
        projects = await self._catalog.list_projects()
        return tuple(await asyncio.gather(*(self._with_workspace_state(item) for item in projects)))

    async def create_project(self, request: ProjectCreateRequest) -> ProductProject:
        project_id = await self._evidence_store.ensure_project(
            repository_url=str(request.repository_url),
            default_branch=request.default_branch,
        )
        await asyncio.to_thread(
            self._provisioner.provision,
            project_id,
            repository_url=str(request.repository_url),
            default_branch=request.default_branch,
        )
        project = await self._catalog.get_project(project_id)
        return await self._with_workspace_state(project)

    async def get_project(self, project_id: UUID) -> ProductProject:
        return await self._with_workspace_state(await self._catalog.get_project(project_id))

    async def list_runs(self, *, project_id: UUID | None = None) -> tuple[ProductRun, ...]:
        if project_id is not None:
            await self._catalog.get_project(project_id)
        return await self._catalog.list_runs(project_id=project_id)

    async def create_run(self, request: RunCreateRequest) -> RunLaunchResponse:
        project = await self._catalog.get_project(request.project_id)
        ready = await asyncio.to_thread(self._provisioner.is_ready, request.project_id)
        if not ready:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not ready for project {request.project_id}"
            )
        try:
            workspace = self._workspace_resolver.resolve(request.project_id)
            base_commit = await asyncio.to_thread(workspace.head_commit)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {request.project_id}"
            ) from exc
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
        self._validate_run_token_budget_plan(dag)
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
            receipt = await self._dispatcher.dispatch(run_id=run_id, task_id=request.task.task_id)
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

    async def _preflight_workspace(
        self,
        workspace: LocalGitWorkspace,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> ProductDependencyPreflight | None:
        if self._dependency_preflight is None:
            return None
        if verification_commands:
            report = await asyncio.to_thread(
                self._dependency_preflight.check,
                workspace.root,
                verification_commands=verification_commands,
            )
        else:
            # Keep the established dependency-only preflight protocol for callers that do not
            # yet have planned verification commands.
            report = await asyncio.to_thread(self._dependency_preflight.check, workspace.root)
        return ProductDependencyPreflight.model_validate(report.model_dump(mode="python"))

    async def get_dependency_environment(
        self,
        project_id: UUID,
    ) -> ProductDependencyEnvironmentStatus:
        preflight = self._require_dependency_preflight()
        await self._catalog.get_project(project_id)
        workspace = self._workspace_resolver.resolve(project_id)
        report = await asyncio.to_thread(preflight.status, workspace.root)
        return ProductDependencyEnvironmentStatus.model_validate(report.model_dump(mode="python"))

    async def rebuild_dependency_environment(
        self,
        project_id: UUID,
    ) -> ProductDependencyEnvironmentStatus:
        preflight = self._require_dependency_preflight()
        await self._catalog.get_project(project_id)
        workspace = self._workspace_resolver.resolve(project_id)
        report = await asyncio.to_thread(preflight.rebuild, workspace.root)
        return ProductDependencyEnvironmentStatus.model_validate(report.model_dump(mode="python"))

    async def dependency_environment_metrics(self) -> ProductDependencyEnvironmentMetrics:
        report = await asyncio.to_thread(self._require_dependency_preflight().metrics)
        return ProductDependencyEnvironmentMetrics.model_validate(report.model_dump(mode="python"))

    async def cleanup_dependency_environments(self) -> ProductDependencyCacheCleanup:
        report = await asyncio.to_thread(self._require_dependency_preflight().cleanup)
        return ProductDependencyCacheCleanup(
            removed_fingerprints=report.removed_fingerprints,
            reclaimed_bytes=report.reclaimed_bytes,
            retained_bytes=report.retained_bytes,
        )

    def _require_dependency_preflight(self) -> DependencyEnvironmentPreflight:
        if self._dependency_preflight is None:
            raise ProductDependencyEnvironmentUnavailableError(
                "dependency environment controls are not configured"
            )
        return self._dependency_preflight

    async def get_run(self, run_id: UUID) -> ProductRunDetail:
        snapshot = await self._evidence_store.load_run(run_id)
        evidence_counts: dict[str, int] = {}
        for evidence in snapshot.evidence:
            if evidence.task_id is not None:
                evidence_counts[evidence.task_id] = evidence_counts.get(evidence.task_id, 0) + 1
        tasks = tuple(
            ProductTaskSummary(
                task_id=item.task.task_id,
                objective=item.task.objective,
                evidence_count=evidence_counts.get(item.task.task_id, 0),
            )
            for item in snapshot.tasks
        )
        return ProductRunDetail(
            run_id=snapshot.run_id,
            project_id=snapshot.project_id,
            repository_url=snapshot.repository_url,
            default_branch=snapshot.default_branch,
            status=snapshot.status,
            base_commit=snapshot.base_commit,
            task_count=len(tasks),
            started_at=snapshot.started_at,
            finished_at=snapshot.finished_at,
            tasks=tasks,
            failures=self._failure_summaries(snapshot),
            checkpoint=self._checkpoint_summary(snapshot),
        )

    @staticmethod
    def _checkpoint_summary(snapshot: PersistedRunSnapshot) -> ProductRunCheckpoint | None:
        """Expose a continuation point only when no successful sibling makes it ambiguous."""

        if snapshot.status.value != "FAILED":
            return None
        executions: list[WorkerExecutionEvidence] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            try:
                executions.append(WorkerExecutionEvidence.model_validate(evidence.payload))
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted worker execution evidence failed schema validation"
                ) from exc
        checkpoints = [item.checkpoint for item in executions if item.checkpoint is not None]
        if len(checkpoints) != 1 or any(
            item.status is WorkerExecutionStatus.SUCCEEDED for item in executions
        ):
            return None
        checkpoint = checkpoints[0]
        assert checkpoint is not None
        if checkpoint.base_commit != snapshot.base_commit:
            raise PersistenceCorruptionError(
                "checkpoint does not descend from the persisted run base"
            )
        return ProductRunCheckpoint(
            task_id=checkpoint.task_id,
            commit_sha=checkpoint.commit_sha,
            changed_files=checkpoint.changed_files,
            reason=checkpoint.reason,
            summary=checkpoint.summary,
            remaining_budget_tokens=checkpoint.remaining_budget_tokens,
        )

    @staticmethod
    def _failure_summaries(snapshot: PersistedRunSnapshot) -> tuple[ProductRunFailure, ...]:
        """Project durable failure records without exposing full agent/tool transcripts."""

        reports: list[tuple[str | None, FailureReport]] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            try:
                execution = WorkerExecutionEvidence.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted worker execution evidence failed schema validation"
                ) from exc
            if execution.run_id != snapshot.run_id or execution.task_id != evidence.task_id:
                raise PersistenceCorruptionError(
                    "persisted worker execution evidence has mismatched Run/Task identity"
                )
            reports.extend((execution.task_id, report) for report in execution.failures)

        # A terminal worker report can describe a later runtime stop (for example an old Repair
        # Agent iteration limit). Include its preceding deterministic failure as well, so the UI
        # and optional AI explanation retain the actionable root cause rather than only the
        # downstream symptom.
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.VERIFICATION_RESULT:
                continue
            try:
                verification = VerificationResult.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted verification evidence failed schema validation"
                ) from exc
            reports.extend(
                (evidence.task_id, report)
                for report in DeterministicVerifier.failure_reports(verification)
            )

        # An interrupted worker can persist a standalone report before terminal execution.
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.FAILURE_REPORT:
                continue
            try:
                reports.append((evidence.task_id, FailureReport.model_validate(evidence.payload)))
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted failure report evidence failed schema validation"
                ) from exc

        summaries: list[ProductRunFailure] = []
        seen: set[tuple[str | None, str, str, tuple[str, ...]]] = set()
        for task_id, report in reports:
            public_evidence = [
                item
                for evidence in report.evidence
                if (item := ProductRuntimeService._public_failure_evidence(evidence)) is not None
            ]
            identity = (
                task_id,
                report.failure_type.value,
                report.message,
                tuple(public_evidence),
            )
            if identity in seen:
                continue
            seen.add(identity)
            summaries.append(
                ProductRunFailure(
                    task_id=task_id,
                    failure_type=report.failure_type,
                    source=report.source,
                    message=report.message[:512],
                    retryable=report.retryable,
                    evidence=tuple(public_evidence[:8]),
                )
            )
            if len(summaries) == 8:
                break
        return tuple(summaries)

    @staticmethod
    def _public_failure_evidence(value: str) -> str | None:
        """Allow only concise diagnostic keys useful in the product UI."""

        key, separator, detail = value.partition("=")
        if not separator or key not in {
            "attempt",
            "check",
            "command",
            "exit_code",
            "stderr",
            "stop_reason",
            "target_failures",
            "repair_attempts_exhausted",
            "execution_backend",
        }:
            return None
        return f"{key}={detail[:480]}"

    async def get_run_metrics(self, run_id: UUID) -> ProductRunMetrics:
        """Return bounded descriptive metrics without deriving Run success."""

        snapshot = await self._evidence_store.load_run(run_id)
        events: list[PersistedRuntimeEvent] = []
        cursor = 0

        while len(events) < MAX_METRIC_EVENT_SCAN:
            remaining = MAX_METRIC_EVENT_SCAN - len(events)
            page_limit = min(METRIC_EVENT_PAGE_SIZE, remaining)
            batch = await self._evidence_store.list_runtime_events(
                run_id,
                after_sequence=cursor,
                limit=page_limit,
            )
            if not batch:
                break
            events.extend(batch)
            cursor = batch[-1].sequence
            if len(batch) < page_limit:
                break

        if len(events) == MAX_METRIC_EVENT_SCAN:
            overflow = await self._evidence_store.list_runtime_events(
                run_id,
                after_sequence=cursor,
                limit=1,
            )
            if overflow:
                raise ProductMetricsUnavailableError(
                    "Run Metrics exceed the bounded runtime-event scan limit"
                )

        aggregate = aggregate_runtime_events(run_id, tuple(events))
        if self._token_budget_store is None:
            raise ProductMetricsUnavailableError("Run token budget store is not configured")
        token_budget = await self._token_budget_store.snapshot(run_id)
        return build_run_metrics(
            snapshot,
            aggregate,
            token_budget,
            workflow_activation_mode=self._workflow_activation_mode,
        )

    async def get_run_dag(self, run_id: UUID) -> ProductRunDAG:
        """Project immutable DAG topology plus evidence-backed presentation state."""

        snapshot, persisted_dag = await asyncio.gather(
            self._evidence_store.load_run(run_id),
            self._dag_store.load_dag(run_id),
        )
        dag = persisted_dag.dag
        order = dag.topological_order()
        latest_states = self._latest_task_states(snapshot)
        completed = {
            task_id for task_id, state in latest_states.items() if state is TaskRunState.SUCCEEDED
        }
        failed = {
            task_id for task_id, state in latest_states.items() if state is TaskRunState.FAILED
        }
        try:
            blocked = set(dag.blocked_task_ids(failed_task_ids=failed))
            ready = set(
                dag.ready_task_ids(
                    completed_task_ids=completed,
                    failed_task_ids=failed,
                )
            )
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted Run DAG state evidence is inconsistent: {exc}"
            ) from exc

        advanced_blocked = {
            task_id
            for task_id in blocked
            if latest_states.get(task_id) not in {None, TaskRunState.PENDING}
        }
        if advanced_blocked:
            raise PersistenceCorruptionError(
                "persisted Run DAG has active or terminal tasks downstream of failed tasks: "
                + ", ".join(sorted(advanced_blocked))
            )

        layers = self._dag_layers(dag, order)
        workflow_records = self._latest_workflow_records(snapshot)
        package_budgets = {}
        if self._token_budget_store is not None:
            token_budget = await self._token_budget_store.snapshot(run_id)
            package_budgets = {item.task_id: item for item in token_budget.work_packages}
        node_items: list[ProductDAGNode] = []
        for index, task_id in enumerate(order):
            presentation_state, state_basis = self._presentation_state(
                task_id,
                latest_states=latest_states,
                ready=ready,
                blocked=blocked,
            )
            contract_block_reason = None
            if (
                presentation_state in {ProductDAGNodeState.READY, ProductDAGNodeState.BLOCKED}
                and self._interface_contract_registry is not None
                and self._work_package_activation_mode is WorkPackageActivationMode.CONTRACT_GATED
            ):
                gate = await self._interface_contract_registry.gate_for_task(
                    run_id=run_id,
                    task_id=task_id,
                )
                if not gate.allowed:
                    presentation_state = ProductDAGNodeState.BLOCKED_BY_CONTRACT
                    state_basis = ProductDAGStateBasis.DERIVED_DAG
                    contract_block_reason = gate.reason
            workflow = workflow_records.get(task_id)
            node_items.append(
                ProductDAGNode(
                    task_id=task_id,
                    objective=dag.node(task_id).task.objective,
                    depends_on=dag.node(task_id).depends_on,
                    topological_index=index,
                    layer=layers[task_id],
                    presentation_state=presentation_state,
                    state_basis=state_basis,
                    execution_mode=(
                        workflow.mode if workflow is not None else dag.node(task_id).execution_mode
                    ),
                    workflow_id=workflow.workflow_id if workflow is not None else None,
                    workflow_step=(
                        workflow.steps[-1].name
                        if workflow is not None and workflow.steps
                        else self._workflow_step_for_state(
                            latest_states.get(task_id),
                            execution_mode=dag.node(task_id).execution_mode,
                        )
                    ),
                    agent_escalation_reason=(
                        workflow.fallback_reason if workflow is not None else None
                    ),
                    contract_block_reason=contract_block_reason,
                    owned_paths=tuple(dag.node(task_id).task.writable_files),
                    consumes=dag.node(task_id).consumes,
                    produces=dag.node(task_id).produces,
                    verification_commands=tuple(dag.node(task_id).task.verification_commands),
                    complexity=(
                        dag.node(task_id).complexity.value
                        if dag.node(task_id).complexity is not None
                        else None
                    ),
                    package_budget_tokens=(
                        package_budgets[task_id].total_budget_tokens
                        if task_id in package_budgets
                        else None
                    ),
                    package_used_tokens=(
                        package_budgets[task_id].developer_used_tokens
                        + package_budgets[task_id].repair_used_tokens
                        if task_id in package_budgets
                        else None
                    ),
                )
            )
        nodes = tuple(node_items)

        order_index = {task_id: index for index, task_id in enumerate(order)}
        edges = tuple(
            ProductDAGEdge(source_task_id=dependency, target_task_id=task_id)
            for task_id in order
            for dependency in sorted(
                dag.node(task_id).depends_on,
                key=order_index.__getitem__,
            )
        )
        return ProductRunDAG(
            run_id=run_id,
            dag_sha256=persisted_dag.dag_sha256,
            topology_source=persisted_dag.source,
            topological_order=tuple(order),
            nodes=nodes,
            edges=edges,
        )

    @staticmethod
    def _workflow_step_for_state(
        state: TaskRunState | None,
        *,
        execution_mode: WorkflowExecutionMode,
    ) -> str | None:
        """Offer a bounded live Workflow stage before its terminal record is persisted."""

        if execution_mode is not WorkflowExecutionMode.WORKFLOW:
            return None
        return {
            None: "等待执行",
            TaskRunState.PENDING: "等待执行",
            TaskRunState.RUNNING: "创建文件",
            TaskRunState.VERIFYING: "验证",
            TaskRunState.REPAIRING: "确定性重试",
            TaskRunState.REVIEWING: "检查模板契约",
        }.get(state)

    @staticmethod
    def _latest_workflow_records(
        snapshot: PersistedRunSnapshot,
    ) -> dict[str, WorkflowExecutionRecord]:
        records: dict[str, WorkflowExecutionRecord] = {}
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.WORKFLOW_EXECUTION:
                continue
            decoded = decode_evidence(item.kind, item.payload)
            if not isinstance(decoded, WorkflowExecutionRecord):
                raise PersistenceCorruptionError(
                    "WORKFLOW_EXECUTION decoded to an unexpected model"
                )
            records[decoded.task_id] = decoded
        return records

    async def get_task_diff(
        self,
        run_id: UUID,
        task_id: str,
        *,
        kind: ProductDiffKind = ProductDiffKind.TASK,
    ) -> ProductTaskDiff:
        """Resolve a bounded read-only Git diff from accepted persisted commit evidence."""

        snapshot = await self._evidence_store.load_run(run_id)
        if not any(item.task.task_id == task_id for item in snapshot.tasks):
            raise ValueError(f"task {task_id!r} does not belong to run {run_id}")

        pair = (
            self._task_commit_pair(snapshot, task_id)
            if kind is ProductDiffKind.TASK
            else self._integration_commit_pair(snapshot, task_id)
        )
        try:
            workspace = self._workspace_resolver.resolve(snapshot.project_id)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductWorkspaceNotReadyError(
                f"managed workspace is not trustworthy for project {snapshot.project_id}"
            ) from exc

        reader = ReadOnlyCommitDiffReader(workspace)
        try:
            if kind is ProductDiffKind.TASK:
                parents = await asyncio.to_thread(reader.commit_parents, pair.head_commit)
                if parents != (pair.base_commit,):
                    raise PersistenceCorruptionError(
                        "persisted task commit no longer has its recorded task base as sole parent"
                    )
            else:
                if pair.task_commit is None or pair.task_base_commit is None:
                    raise PersistenceCorruptionError(
                        "integration diff lacks task commit provenance"
                    )
                task_parents = await asyncio.to_thread(reader.commit_parents, pair.task_commit)
                if task_parents != (pair.task_base_commit,):
                    raise PersistenceCorruptionError(
                        "persisted integrated task commit no longer matches its recorded task base"
                    )
                integration_parents = await asyncio.to_thread(
                    reader.commit_parents,
                    pair.head_commit,
                )
                if integration_parents != (pair.base_commit, pair.task_commit):
                    raise PersistenceCorruptionError(
                        "persisted integration commit no longer matches its recorded parent pair"
                    )
            diff = await asyncio.to_thread(
                reader.read,
                base_commit=pair.base_commit,
                head_commit=pair.head_commit,
            )
        except CommitDiffError as exc:
            raise PersistenceCorruptionError(
                "persisted Git diff evidence cannot be reproduced from the managed repository"
            ) from exc

        return ProductTaskDiff(
            run_id=snapshot.run_id,
            project_id=snapshot.project_id,
            task_id=task_id,
            diff_kind=kind,
            evidence_basis=(
                ProductDiffEvidenceBasis.WORKER_EXECUTION
                if kind is ProductDiffKind.TASK
                else ProductDiffEvidenceBasis.MERGE_QUEUE_SNAPSHOT
            ),
            source_evidence_id=pair.evidence_id,
            source_evidence_sha256=pair.evidence_sha256,
            base_commit=diff.base_commit,
            head_commit=diff.head_commit,
            changed_file_count=diff.changed_file_count,
            additions=diff.additions,
            deletions=diff.deletions,
            files=tuple(
                ProductDiffFile(
                    path=item.path,
                    status=ProductDiffFileStatus(item.status.value),
                    additions=item.additions,
                    deletions=item.deletions,
                    binary=item.binary,
                    patch=item.patch,
                    patch_bytes=item.patch_bytes,
                    patch_sha256=item.patch_sha256,
                    patch_truncated=item.patch_truncated,
                    patch_omitted_reason=(
                        ProductDiffOmissionReason(item.patch_omitted_reason.value)
                        if item.patch_omitted_reason is not None
                        else None
                    ),
                )
                for item in diff.files
            ),
            omitted_file_count=diff.omitted_file_count,
            patch_bytes=diff.patch_bytes,
            truncated=diff.truncated,
        )

    async def get_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]:
        """Read the accepted monotonic event projection without changing runtime state."""

        return await self._evidence_store.list_runtime_events(
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def get_task(self, run_id: UUID, task_id: str) -> ProductTaskDetail:
        snapshot = await self._evidence_store.load_run(run_id)
        persisted = next((item for item in snapshot.tasks if item.task.task_id == task_id), None)
        if persisted is None:
            raise ValueError(f"task {task_id!r} does not belong to run {run_id}")
        evidence = tuple(
            ProductEvidenceSummary(
                evidence_id=item.id,
                kind=item.kind,
                stage=item.stage,
                sequence=item.sequence,
                payload_sha256=item.payload_sha256,
                created_at=item.created_at,
            )
            for item in snapshot.evidence
            if item.task_id == task_id
        )
        return ProductTaskDetail(
            run_id=snapshot.run_id,
            project_id=snapshot.project_id,
            run_status=snapshot.status,
            task=persisted.task,
            contract_sha256=persisted.contract_sha256,
            created_at=persisted.created_at,
            evidence=evidence,
        )

    @staticmethod
    def _task_commit_pair(snapshot: PersistedRunSnapshot, task_id: str) -> _DiffCommitPair:
        candidates: list[_DiffCommitPair] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            if evidence.task_id != task_id:
                continue
            try:
                execution = WorkerExecutionEvidence.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    "persisted worker execution evidence failed schema validation"
                ) from exc
            if execution.run_id != snapshot.run_id or execution.task_id != task_id:
                raise PersistenceCorruptionError(
                    "persisted worker execution evidence has mismatched Run/Task identity"
                )
            if execution.status is not WorkerExecutionStatus.SUCCEEDED:
                continue
            if execution.commit_sha is None:
                raise PersistenceCorruptionError("successful worker evidence lacks task commit")
            candidates.append(
                _DiffCommitPair(
                    base_commit=execution.base_commit,
                    head_commit=execution.commit_sha,
                    evidence_id=evidence.id,
                    evidence_sha256=evidence.payload_sha256,
                )
            )
        if not candidates:
            raise ProductDiffUnavailableError(
                "task diff is not available until task "
                f"{task_id!r} has accepted successful worker evidence"
            )
        unique = {(item.base_commit, item.head_commit) for item in candidates}
        if len(unique) != 1:
            raise PersistenceCorruptionError(
                "persisted successful worker evidence defines conflicting task commit pairs"
            )
        return max(candidates, key=lambda item: item.evidence_id)

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
                if (
                    attempt.task_id != task_id
                    or attempt.outcome is not MergeAttemptOutcome.INTEGRATED
                ):
                    continue
                if attempt.integration_commit is None:
                    raise PersistenceCorruptionError(
                        "integrated merge attempt lacks an integration commit"
                    )
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

    async def _with_workspace_state(self, project: ProductProject) -> ProductProject:
        ready = await asyncio.to_thread(self._provisioner.is_ready, project.project_id)
        return project.model_copy(update={"workspace_ready": ready})

    @staticmethod
    def _latest_task_states(snapshot: PersistedRunSnapshot) -> dict[str, TaskRunState]:
        latest: dict[str, tuple[int, TaskRunState]] = {}
        for evidence in snapshot.evidence:
            if (
                evidence.kind is not PersistenceEvidenceKind.STATE_TRANSITION
                or evidence.task_id is None
            ):
                continue
            event = RunEvent.model_validate(evidence.payload)
            sequence = evidence.sequence if evidence.sequence is not None else event.sequence
            current = latest.get(evidence.task_id)
            if current is None or sequence > current[0]:
                latest[evidence.task_id] = (sequence, event.state)
        return {task_id: state for task_id, (_, state) in latest.items()}

    @staticmethod
    def _dag_layers(dag: TaskDAG, order: Sequence[str]) -> dict[str, int]:
        layers: dict[str, int] = {}
        for task_id in order:
            dependencies = dag.node(task_id).depends_on
            layers[task_id] = (
                0
                if not dependencies
                else 1 + max(layers[dependency] for dependency in dependencies)
            )
        return layers

    @staticmethod
    def _presentation_state(
        task_id: str,
        *,
        latest_states: dict[str, TaskRunState],
        ready: set[str],
        blocked: set[str],
    ) -> tuple[ProductDAGNodeState, ProductDAGStateBasis]:
        latest = latest_states.get(task_id)
        if latest not in {None, TaskRunState.PENDING}:
            return ProductDAGNodeState(latest.value), ProductDAGStateBasis.EVIDENCE
        if task_id in blocked:
            return ProductDAGNodeState.BLOCKED, ProductDAGStateBasis.DERIVED_DAG
        if task_id in ready:
            return ProductDAGNodeState.READY, ProductDAGStateBasis.DERIVED_DAG
        if latest is TaskRunState.PENDING:
            return ProductDAGNodeState.PENDING, ProductDAGStateBasis.EVIDENCE
        return ProductDAGNodeState.PENDING, ProductDAGStateBasis.DERIVED_DAG
