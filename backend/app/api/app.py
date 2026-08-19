from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from app.api.models import (
    ProductProject,
    ProductRun,
    ProductRunDetail,
    ProductTaskDetail,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.api.service import ProductRuntimeService
from app.workspace import ProjectProvisionError, WorkspaceGitError


def create_app(service: ProductRuntimeService, *, close_service: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if close_service:
                await service.dispose()

    app = FastAPI(
        title="DevFlow API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/projects", response_model=list[ProductProject])
    async def list_projects() -> tuple[ProductProject, ...]:
        return await service.list_projects()

    @app.post(
        "/api/v1/projects",
        response_model=ProductProject,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project(request: ProjectCreateRequest) -> ProductProject:
        try:
            return await service.create_project(request)
        except (ProjectProvisionError, WorkspaceGitError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/projects/{project_id}", response_model=ProductProject)
    async def get_project(project_id: UUID) -> ProductProject:
        try:
            return await service.get_project(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/runs", response_model=list[ProductRun])
    async def list_runs(
        project_id: Annotated[UUID | None, Query()] = None,
    ) -> tuple[ProductRun, ...]:
        try:
            return await service.list_runs(project_id=project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs",
        response_model=RunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_run(request: RunCreateRequest) -> RunLaunchResponse:
        try:
            return await service.create_run(request)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkspaceGitError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}", response_model=ProductRunDetail)
    async def get_run(run_id: UUID) -> ProductRunDetail:
        try:
            return await service.get_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/runs/{run_id}/tasks/{task_id}",
        response_model=ProductTaskDetail,
    )
    async def get_task(run_id: UUID, task_id: str) -> ProductTaskDetail:
        try:
            return await service.get_task(run_id, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
