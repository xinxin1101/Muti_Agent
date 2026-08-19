from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.api.models import (
    DispatchStatus,
    ProductDAGEdge,
    ProductDAGNode,
    ProductDAGNodeState,
    ProductDAGStateBasis,
    ProductDiffEvidenceBasis,
    ProductDiffFile,
    ProductDiffFileStatus,
    ProductDiffKind,
    ProductDiffOmissionReason,
    ProductEvidenceSummary,
    ProductProject,
    ProductRun,
    ProductRunDAG,
    ProductRunDetail,
    ProductTaskDetail,
    ProductTaskDiff,
    ProductTaskSummary,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG, TaskNode
from app.models.dispatch import (
    TaskDispatchReceipt,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.events import PersistedRuntimeEvent
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.run import RunEvent, TaskRunState
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedRunSnapshot,
    PersistenceEvidenceKind,
)
from app.workspace import (
    CommitDiffError,
    LocalGitWorkspace,
    ReadOnlyCommitDiffReader,
    WorkspaceGitError,
)


class ProductWorkspaceNotReadyError(RuntimeError):
    """Raised when a persisted Project has no trustworthy managed Git workspace."""


class ProductDiffUnavailableError(RuntimeError):
    """Raised when accepted Git evidence does not yet define the requested diff."""


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
    ) -> None:
        self._catalog = catalog
        self._evidence_store = evidence_store
        self._dag_store = dag_store
        self._provisioner = provisioner
        self._workspace_resolver = workspace_resolver
        self._dispatcher = dispatcher

    async def dispose(self) -> None:
        await self._catalog.dispose()
        await self._evidence_store.dispose()
        await self._dag_store.dispose()

    async def list_projects(self) -> tuple[ProductProject, ...]:
        projects = await self._catalog.list_projects()
        return tuple(
            await asyncio.gather(*(self._with_workspace_state(item) for item in projects))
        )

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

        dag = TaskDAG(tasks=(TaskNode(task=request.task, depends_on=()),))
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
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
        )

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
            task_id
            for task_id, state in latest_states.items()
            if state is TaskRunState.SUCCEEDED
        }
        failed = {
            task_id
            for task_id, state in latest_states.items()
            if state is TaskRunState.FAILED
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
        node_items: list[ProductDAGNode] = []
        for index, task_id in enumerate(order):
            presentation_state, state_basis = self._presentation_state(
                task_id,
                latest_states=latest_states,
                ready=ready,
                blocked=blocked,
            )
            node_items.append(
                ProductDAGNode(
                    task_id=task_id,
                    objective=dag.node(task_id).task.objective,
                    depends_on=dag.node(task_id).depends_on,
                    topological_index=index,
                    layer=layers[task_id],
                    presentation_state=presentation_state,
                    state_basis=state_basis,
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
