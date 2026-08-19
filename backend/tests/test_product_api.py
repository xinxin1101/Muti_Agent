from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.api import create_app
from app.api.models import (
    DispatchStatus,
    ProductDAGNode,
    ProductDAGNodeState,
    ProductDAGStateBasis,
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
from app.api.service import ProductWorkspaceNotReadyError
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
        self.workspace_ready = True

    async def dispose(self) -> None:
        return None

    def project(self) -> ProductProject:
        return ProductProject(
            project_id=self.project_id,
            repository_url="https://github.com/example/repo",
            default_branch="main",
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
            run_count=1,
            workspace_ready=self.workspace_ready,
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
        if not self.workspace_ready:
            raise ProductWorkspaceNotReadyError("managed workspace is not ready")
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

    async def get_run_dag(self, run_id: UUID):
        if run_id != self.run_id:
            raise ValueError("unknown run")
        return ProductRunDAG(
            run_id=self.run_id,
            dag_sha256="d" * 64,
            topology_source="PERSISTED",
            topological_order=("api-task",),
            nodes=(
                ProductDAGNode(
                    task_id="api-task",
                    objective="Expose a validated product API.",
                    topological_index=0,
                    layer=0,
                    presentation_state=ProductDAGNodeState.READY,
                    state_basis=ProductDAGStateBasis.DERIVED_DAG,
                ),
            ),
            edges=(),
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


async def _request(method: str, path: str, *, service: FakeProductService, json=None):
    transport = httpx.ASGITransport(app=create_app(service))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, json=json)


def test_product_routes_expose_typed_browser_contracts() -> None:
    service = FakeProductService()

    assert asyncio.run(_request("GET", "/healthz", service=service)).json() == {"status": "ok"}
    projects = asyncio.run(_request("GET", "/api/v1/projects", service=service))
    assert projects.status_code == 200
    assert projects.json()[0]["workspace_ready"] is True

    runs = asyncio.run(
        _request("GET", f"/api/v1/runs?project_id={service.project_id}", service=service)
    )
    assert runs.status_code == 200
    assert runs.json()[0]["status"] == "RUNNING"

    dashboard = asyncio.run(_request("GET", f"/api/v1/runs/{service.run_id}", service=service))
    assert dashboard.status_code == 200
    assert dashboard.json()["tasks"][0]["task_id"] == "api-task"

    dag = asyncio.run(
        _request("GET", f"/api/v1/runs/{service.run_id}/dag", service=service)
    )
    assert dag.status_code == 200
    assert dag.json()["nodes"][0]["presentation_state"] == "READY"
    assert dag.json()["edges"] == []

    task = asyncio.run(
        _request("GET", f"/api/v1/runs/{service.run_id}/tasks/api-task", service=service)
    )
    assert task.status_code == 200
    assert task.json()["task"]["verification_commands"] == ["pytest -q"]


def test_project_and_run_creation_use_validated_requests() -> None:
    service = FakeProductService()
    created = asyncio.run(
        _request(
            "POST",
            "/api/v1/projects",
            service=service,
            json={
                "repository_url": "https://github.com/example/repo",
                "default_branch": "main",
            },
        )
    )
    assert created.status_code == 201
    assert service.created_request is not None

    launched = asyncio.run(
        _request(
            "POST",
            "/api/v1/runs",
            service=service,
            json={
                "project_id": str(service.project_id),
                "task": _task().model_dump(mode="json"),
            },
        )
    )
    assert launched.status_code == 201
    assert launched.json()["dispatch_status"] == "QUEUED"
    assert service.run_request is not None
    assert "base_commit" not in service.run_request.model_dump()


def test_workspace_not_ready_maps_to_conflict() -> None:
    service = FakeProductService()
    service.workspace_ready = False
    response = asyncio.run(
        _request(
            "POST",
            "/api/v1/runs",
            service=service,
            json={"project_id": str(service.project_id), "task": _task().model_dump(mode="json")},
        )
    )
    assert response.status_code == 409


def test_unknown_product_resources_map_to_404() -> None:
    service = FakeProductService()
    missing = asyncio.run(_request("GET", f"/api/v1/runs/{uuid4()}", service=service))
    assert missing.status_code == 404

    missing_dag = asyncio.run(
        _request("GET", f"/api/v1/runs/{uuid4()}/dag", service=service)
    )
    assert missing_dag.status_code == 404

    missing_task = asyncio.run(
        _request("GET", f"/api/v1/runs/{service.run_id}/tasks/missing", service=service)
    )
    assert missing_task.status_code == 404


def test_product_run_rejects_unknown_persistence_status() -> None:
    with pytest.raises(ValidationError):
        ProductRun(
            run_id=uuid4(),
            project_id=uuid4(),
            status="NOT_A_RUNTIME_STATUS",
            base_commit="a" * 40,
            task_count=1,
            started_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
