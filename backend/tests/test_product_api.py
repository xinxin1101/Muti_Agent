from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.api import create_app
from app.api.models import (
    DispatchStatus,
    ProductProject,
    ProductRun,
    ProductRunDetail,
    ProductTaskDetail,
    ProductTaskSummary,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.models import TaskContract


def _task() -> TaskContract:
    return TaskContract(
        task_id="api-task",
        objective="Expose a validated product API.",
        readable_files=["backend/app/**"],
        writable_files=["backend/app/api/**"],
        readonly_files=["backend/tests/**"],
        acceptance_criteria=["The browser uses typed backend contracts."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


class FakeProductService:
    def __init__(self) -> None:
        self.project_id = uuid4()
        self.run_id = uuid4()
        self.created_request: ProjectCreateRequest | None = None
        self.run_request: RunCreateRequest | None = None

    async def dispose(self) -> None:
        return None

    def project(self) -> ProductProject:
        return ProductProject(
            project_id=self.project_id,
            repository_url="https://github.com/example/repo",
            default_branch="main",
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
            run_count=1,
            workspace_ready=True,
        )

    async def list_projects(self):
        return (self.project(),)

    async def create_project(self, request: ProjectCreateRequest):
        self.created_request = request
        return self.project()

    async def get_project(self, project_id: UUID):
        if project_id != self.project_id:
            raise ValueError("unknown project")
        return self.project()

    async def list_runs(self, *, project_id: UUID | None = None):
        if project_id is not None and project_id != self.project_id:
            raise ValueError("unknown project")
        return (
            ProductRun(
                run_id=self.run_id,
                project_id=self.project_id,
                status="RUNNING",
                base_commit="a" * 40,
                task_count=1,
                started_at=datetime(2026, 8, 19, tzinfo=UTC),
            ),
        )

    async def create_run(self, request: RunCreateRequest):
        self.run_request = request
        return RunLaunchResponse(
            run_id=self.run_id,
            project_id=self.project_id,
            task_id=request.task.task_id,
            base_commit="a" * 40,
            dispatch_status=DispatchStatus.QUEUED,
            dispatch_id=uuid4(),
            broker_message_id="message-1",
            queue_name="devflow_tasks",
        )

    async def get_run(self, run_id: UUID):
        if run_id != self.run_id:
            raise ValueError("unknown run")
        return ProductRunDetail(
            run_id=self.run_id,
            project_id=self.project_id,
            repository_url="https://github.com/example/repo",
            default_branch="main",
            status="RUNNING",
            base_commit="a" * 40,
            task_count=1,
            started_at=datetime(2026, 8, 19, tzinfo=UTC),
            tasks=(
                ProductTaskSummary(
                    task_id="api-task",
                    objective="Expose a validated product API.",
                    evidence_count=0,
                ),
            ),
        )

    async def get_task(self, run_id: UUID, task_id: str):
        if run_id != self.run_id or task_id != "api-task":
            raise ValueError("unknown task")
        return ProductTaskDetail(
            run_id=self.run_id,
            project_id=self.project_id,
            run_status="RUNNING",
            task=_task(),
            contract_sha256="b" * 64,
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
            evidence=(),
        )


def test_product_routes_expose_typed_browser_contracts() -> None:
    service = FakeProductService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]

    assert client.get("/healthz").json() == {"status": "ok"}

    projects = client.get("/api/v1/projects")
    assert projects.status_code == 200
    assert projects.json()[0]["workspace_ready"] is True

    runs = client.get(f"/api/v1/runs?project_id={service.project_id}")
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "RUNNING"

    dashboard = client.get(f"/api/v1/runs/{service.run_id}")
    assert dashboard.status_code == 200
    assert dashboard.json()["tasks"][0]["task_id"] == "api-task"

    task = client.get(f"/api/v1/runs/{service.run_id}/tasks/api-task")
    assert task.status_code == 200
    assert task.json()["task"]["verification_commands"] == ["pytest -q"]


def test_project_and_run_creation_use_validated_requests() -> None:
    service = FakeProductService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]

    created = client.post(
        "/api/v1/projects",
        json={
            "repository_url": "https://github.com/example/repo",
            "default_branch": "main",
        },
    )
    assert created.status_code == 201
    assert service.created_request is not None

    launched = client.post(
        "/api/v1/runs",
        json={
            "project_id": str(service.project_id),
            "task": _task().model_dump(mode="json"),
        },
    )
    assert launched.status_code == 201
    assert launched.json()["dispatch_status"] == "QUEUED"
    assert service.run_request is not None
    assert "base_commit" not in service.run_request.model_dump()


def test_unknown_product_resources_map_to_404() -> None:
    service = FakeProductService()
    client = TestClient(create_app(service))  # type: ignore[arg-type]

    missing = client.get(f"/api/v1/runs/{uuid4()}")
    assert missing.status_code == 404

    missing_task = client.get(f"/api/v1/runs/{service.run_id}/tasks/missing")
    assert missing_task.status_code == 404
