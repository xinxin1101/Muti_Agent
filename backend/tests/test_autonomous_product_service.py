from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.api.autonomous import (
    AutonomousProductRuntimeService,
    RequirementDispatchState,
    RequirementRunCreateRequest,
    RequirementRunLaunchState,
)
from app.api.models import ProductProject
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG, TaskNode
from app.models.dispatch import TaskDispatchReceipt
from app.models.task import TaskContract
from app.persistence.dag import PersistedDAGSnapshot, PersistedDAGSource


def _task(task_id: str, path: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}.",
        readable_files=["app/**"],
        writable_files=[path],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} is externally verifiable."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("root-b", "app/b.py")),
            TaskNode(task=_task("dependent", "app/dependent.py"), depends_on=("root-a", "root-b")),
            TaskNode(task=_task("root-a", "app/a.py")),
        )
    )


class FakePlanner:
    def __init__(self, dag: TaskDAG) -> None:
        self.dag = dag
        self.requirement: str | None = None
        self.repository_context: str | None = None

    async def plan(self, requirement: str, *, repository_context: str | None = None) -> TaskDAG:
        self.requirement = requirement
        self.repository_context = repository_context
        return self.dag


class FakeCatalog:
    def __init__(self, project: ProductProject) -> None:
        self.project = project

    async def get_project(self, project_id: UUID) -> ProductProject:
        if project_id != self.project.project_id:
            raise ValueError("unknown project")
        return self.project

    async def list_projects(self, *, limit: int = 100):
        return (self.project,)

    async def list_runs(self, *, project_id=None, limit: int = 100):
        return ()

    async def dispose(self) -> None:
        return None


class FakeEvidenceStore:
    async def dispose(self) -> None:
        return None


class FakeDAGStore:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.started_dag: TaskDAG | None = None
        self.base_commit: str | None = None

    async def start_run(self, *, project_id, dag, base_commit, run_id=None):
        self.started_dag = dag
        self.base_commit = base_commit
        return self.run_id

    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot:
        assert run_id == self.run_id
        assert self.started_dag is not None
        return PersistedDAGSnapshot(
            run_id=run_id,
            dag=self.started_dag,
            dag_sha256="d" * 64,
            source=PersistedDAGSource.PERSISTED,
        )

    async def dispose(self) -> None:
        return None


class FakeProvisioner:
    def is_ready(self, project_id: UUID) -> bool:
        return True


class FakeWorkspace:
    def head_commit(self) -> str:
        return "c" * 40

    def tracked_files(self) -> list[str]:
        return ["app/a.py", "app/b.py", "app/dependent.py", "tests/test_app.py"]


class FakeResolver:
    def resolve(self, project_id: UUID):
        return FakeWorkspace()


class FakeDispatcher:
    def __init__(self, *, fail_task_id: str | None = None) -> None:
        self.fail_task_id = fail_task_id
        self.dispatched: list[str] = []

    async def dispatch(self, *, run_id: UUID, task_id: str) -> TaskDispatchReceipt:
        self.dispatched.append(task_id)
        if task_id == self.fail_task_id:
            raise TaskDispatchBrokerError("broker unavailable")
        return TaskDispatchReceipt(
            dispatch_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            broker_message_id=f"message-{task_id}",
            queue_name="devflow_tasks",
        )


class FakePublicationStore:
    async def dispose(self) -> None:
        return None


def _service(*, fail_task_id: str | None = None):
    project_id = uuid4()
    project = ProductProject(
        project_id=project_id,
        repository_url="https://github.com/example/repo",
        default_branch="main",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        run_count=0,
        workspace_ready=True,
    )
    planner = FakePlanner(_dag())
    dag_store = FakeDAGStore()
    dispatcher = FakeDispatcher(fail_task_id=fail_task_id)
    service = AutonomousProductRuntimeService(
        catalog=FakeCatalog(project),  # type: ignore[arg-type]
        evidence_store=FakeEvidenceStore(),  # type: ignore[arg-type]
        dag_store=dag_store,  # type: ignore[arg-type]
        provisioner=FakeProvisioner(),  # type: ignore[arg-type]
        workspace_resolver=FakeResolver(),  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        publication_store=FakePublicationStore(),  # type: ignore[arg-type]
        github_publisher=None,
        requirement_planner=planner,
    )
    return service, project_id, planner, dag_store, dispatcher


def test_requirement_run_persists_validated_dag_before_dispatching_only_roots() -> None:
    service, project_id, planner, dag_store, dispatcher = _service()

    result = asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(
                project_id=project_id,
                requirement="Implement the feature as a multi-agent run.",
            )
        )
    )

    assert dag_store.started_dag is not None
    assert dag_store.started_dag.topological_order() == ["root-a", "root-b", "dependent"]
    assert dag_store.base_commit == "c" * 40
    assert dispatcher.dispatched == ["root-a", "root-b"]
    assert result.task_ids == ("root-a", "root-b", "dependent")
    assert result.initial_ready_task_ids == ("root-a", "root-b")
    assert result.launch_state is RequirementRunLaunchState.QUEUED
    assert all(item.state is RequirementDispatchState.QUEUED for item in result.dispatches)
    assert planner.requirement == "Implement the feature as a multi-agent run."
    assert planner.repository_context is not None
    assert "base_commit=" + "c" * 40 in planner.repository_context
    assert "app/dependent.py" in planner.repository_context


def test_requirement_run_reports_partial_broker_failure_without_dispatching_dependencies() -> None:
    service, project_id, _planner, _dag_store, dispatcher = _service(fail_task_id="root-b")

    result = asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(project_id=project_id, requirement="Implement feature.")
        )
    )

    assert dispatcher.dispatched == ["root-a", "root-b"]
    assert result.launch_state is RequirementRunLaunchState.PARTIAL
    failed = next(item for item in result.dispatches if item.task_id == "root-b")
    assert failed.state is RequirementDispatchState.BROKER_UNAVAILABLE
    assert failed.dispatch_id is None
    assert "dependent" not in dispatcher.dispatched
