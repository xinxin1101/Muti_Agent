from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.api.models import (
    DispatchStatus,
    ProductProject,
    ProjectCreateRequest,
    RunCreateRequest,
)
from app.api.service import ProductRuntimeService
from app.dispatch.errors import TaskDispatchBrokerError
from app.models import TaskContract
from app.models.dag import TaskDAG
from app.models.dispatch import TaskDispatchReceipt


def _task() -> TaskContract:
    return TaskContract(
        task_id="service-task",
        objective="Launch through the accepted dispatcher.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Dispatch uses persisted runtime identity."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


class FakeCatalog:
    def __init__(self, project: ProductProject) -> None:
        self.project = project

    async def dispose(self) -> None:
        return None

    async def list_projects(self, *, limit: int = 100):
        return (self.project,)

    async def get_project(self, project_id: UUID):
        if project_id != self.project.project_id:
            raise ValueError("unknown project")
        return self.project

    async def list_runs(self, *, project_id: UUID | None = None, limit: int = 100):
        return ()


class FakeStore:
    def __init__(self, project_id: UUID) -> None:
        self.project_id = project_id

    async def dispose(self) -> None:
        return None

    async def ensure_project(self, *, repository_url: str, default_branch: str, project_id=None):
        return self.project_id

    async def load_run(self, run_id):
        raise AssertionError("not needed")


class FakeDAGStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.started_run_id: UUID | None = None
        self.started_project_id: UUID | None = None
        self.started_base_commit: str | None = None
        self.started_dag: TaskDAG | None = None

    async def dispose(self) -> None:
        return None

    async def start_run(
        self,
        *,
        project_id: UUID,
        dag: TaskDAG,
        base_commit: str,
        run_id: UUID | None = None,
    ) -> UUID:
        if self.fail:
            raise RuntimeError("atomic DAG Run start failed")
        self.started_run_id = run_id or uuid4()
        self.started_project_id = project_id
        self.started_base_commit = base_commit
        self.started_dag = dag
        return self.started_run_id

    async def load_dag(self, run_id: UUID):
        raise AssertionError("not needed")


class FakeProvisioner:
    def __init__(self) -> None:
        self.ready = True
        self.provisioned: UUID | None = None

    def is_ready(self, project_id: UUID) -> bool:
        return self.ready

    def provision(self, project_id: UUID, *, repository_url: str, default_branch: str) -> None:
        self.provisioned = project_id


class FakeWorkspace:
    def head_commit(self) -> str:
        return "c" * 40


class FakeResolver:
    def resolve(self, project_id: UUID) -> FakeWorkspace:
        return FakeWorkspace()


class FakeDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.dispatched: tuple[UUID, str] | None = None

    async def dispatch(self, *, run_id: UUID, task_id: str):
        self.dispatched = (run_id, task_id)
        if self.fail:
            raise TaskDispatchBrokerError("broker unavailable")
        return TaskDispatchReceipt(
            dispatch_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            broker_message_id="message",
            queue_name="devflow_tasks",
        )


def _service(
    *,
    dispatcher: FakeDispatcher | None = None,
    dag_store: FakeDAGStore | None = None,
):
    project_id = uuid4()
    project = ProductProject(
        project_id=project_id,
        repository_url="https://example.com/repo.git",
        default_branch="main",
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
        run_count=0,
        workspace_ready=False,
    )
    store = FakeStore(project_id)
    actual_dag_store = dag_store or FakeDAGStore()
    provisioner = FakeProvisioner()
    actual_dispatcher = dispatcher or FakeDispatcher()
    service = ProductRuntimeService(
        catalog=FakeCatalog(project),  # type: ignore[arg-type]
        evidence_store=store,  # type: ignore[arg-type]
        dag_store=actual_dag_store,  # type: ignore[arg-type]
        provisioner=provisioner,
        workspace_resolver=FakeResolver(),  # type: ignore[arg-type]
        dispatcher=actual_dispatcher,
    )
    return service, store, actual_dag_store, provisioner, actual_dispatcher, project_id


def test_create_project_provisions_backend_managed_workspace() -> None:
    service, _store, _dag, provisioner, _dispatcher, project_id = _service()
    result = asyncio.run(
        service.create_project(
            ProjectCreateRequest(
                repository_url="https://example.com/repo.git",
                default_branch="main",
            )
        )
    )

    assert provisioner.provisioned == project_id
    assert result.workspace_ready is True


def test_create_run_atomically_freezes_dag_before_dispatch() -> None:
    service, _store, dag_store, _provisioner, dispatcher, project_id = _service()
    result = asyncio.run(
        service.create_run(RunCreateRequest(project_id=project_id, task=_task()))
    )

    assert dag_store.started_project_id == project_id
    assert dag_store.started_base_commit == "c" * 40
    assert dag_store.started_run_id is not None
    assert dag_store.started_dag is not None
    assert dag_store.started_dag.task_ids == ["service-task"]
    assert dag_store.started_dag.node("service-task").depends_on == ()
    assert dispatcher.dispatched == (dag_store.started_run_id, "service-task")
    assert result.run_id == dag_store.started_run_id
    assert result.dispatch_status is DispatchStatus.QUEUED
    assert result.base_commit == "c" * 40


def test_atomic_dag_run_start_failure_prevents_dispatch_and_run_identity() -> None:
    dag_store = FakeDAGStore(fail=True)
    service, _store, _dag, _provisioner, dispatcher, project_id = _service(
        dag_store=dag_store
    )

    with pytest.raises(RuntimeError, match="atomic DAG Run start failed"):
        asyncio.run(
            service.create_run(RunCreateRequest(project_id=project_id, task=_task()))
        )

    assert dag_store.started_run_id is None
    assert dispatcher.dispatched is None


def test_broker_failure_is_reported_without_fabricating_runtime_success() -> None:
    dispatcher = FakeDispatcher(fail=True)
    service, _store, _dag, _provisioner, _dispatcher, project_id = _service(
        dispatcher=dispatcher
    )
    result = asyncio.run(
        service.create_run(RunCreateRequest(project_id=project_id, task=_task()))
    )

    assert result.dispatch_status is DispatchStatus.BROKER_UNAVAILABLE
    assert result.dispatch_id is None
    assert result.detail == "broker unavailable"
