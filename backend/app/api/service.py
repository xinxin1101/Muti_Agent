from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.api.models import (
    DispatchStatus,
    ProductDAGEdge,
    ProductDAGNode,
    ProductDAGNodeState,
    ProductDAGStateBasis,
    ProductEvidenceSummary,
    ProductProject,
    ProductRun,
    ProductRunDAG,
    ProductRunDetail,
    ProductTaskDetail,
    ProductTaskSummary,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG, TaskNode
from app.models.dispatch import TaskDispatchReceipt
from app.models.events import PersistedRuntimeEvent
from app.models.run import RunEvent, TaskRunState
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedRunSnapshot,
    PersistenceEvidenceKind,
)
from app.workspace import LocalGitWorkspace, WorkspaceGitError


class ProductWorkspaceNotReadyError(RuntimeError):
    """Raised when a persisted Project has no trustworthy managed Git workspace."""


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
    async def start_run(
        self,
        *,
        project_id: UUID,
        tasks: Sequence,
        base_commit: str,
        run_id: UUID | None = None,
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
    async def persist_dag(self, *, run_id: UUID, dag: TaskDAG) -> PersistedDAGSnapshot: ...
    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot: ...
    async def dispose(self) -> None: ...


class ProductProvisioner(Protocol):
    def provision(self, project_id: UUID, *, repository_url: str, default_branch: str) -> None: ...
    def is_ready(self, project_id: UUID) -> bool: ...


class ProductWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class ProductDispatcher(Protocol):
    async def dispatch(self, *, run_id: UUID, task_id: str) -> TaskDispatchReceipt: ...


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

        run_id = await self._evidence_store.start_run(
            project_id=request.project_id,
            tasks=(request.task,),
            base_commit=base_commit,
        )
        await self._dag_store.persist_dag(
            run_id=run_id,
            dag=TaskDAG(tasks=(TaskNode(task=request.task, depends_on=()),)),
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
