from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from app.api.models import (
    ProductDiffKind,
    ProductProject,
    ProductRun,
    ProductRunDAG,
    ProductRunDetail,
    ProductRunMetrics,
    ProductTaskDetail,
    ProductTaskDiff,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.api.service import (
    ProductDiffUnavailableError,
    ProductMetricsUnavailableError,
    ProductRuntimeService,
    ProductWorkspaceNotReadyError,
)
from app.api.sse import (
    SSE_BATCH_LIMIT,
    RuntimeEventStreamSafetyError,
    resolve_event_cursor,
    runtime_event_stream,
    validate_runtime_event_batch,
)
from app.persistence import PersistenceCorruptionError, PersistenceDAGUnavailableError
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
        allow_headers=["Content-Type", "Last-Event-ID"],
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
        except ProductWorkspaceNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    @app.get("/api/v1/runs/{run_id}/metrics", response_model=ProductRunMetrics)
    async def get_run_metrics(request: Request, run_id: UUID) -> ProductRunMetrics:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="Run Metrics does not accept browser-authored selectors",
            )
        try:
            return await service.get_run_metrics(run_id)
        except ProductMetricsUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted Run Metrics source facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/dag", response_model=ProductRunDAG)
    async def get_run_dag(run_id: UUID) -> ProductRunDAG:
        try:
            return await service.get_run_dag(run_id)
        except PersistenceDAGUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted Run DAG failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/events")
    async def stream_run_events(
        request: Request,
        run_id: UUID,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        try:
            cursor = resolve_event_cursor(
                after_sequence=after_sequence,
                last_event_id=last_event_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            initial_events = await service.get_runtime_events(
                run_id,
                after_sequence=cursor,
                limit=SSE_BATCH_LIMIT,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        try:
            validate_runtime_event_batch(
                initial_events,
                run_id=run_id,
                after_sequence=cursor,
            )
        except RuntimeEventStreamSafetyError as exc:
            raise HTTPException(
                status_code=500,
                detail="runtime event stream safety validation failed",
            ) from exc

        return StreamingResponse(
            runtime_event_stream(
                service,
                request,
                run_id=run_id,
                after_sequence=cursor,
                initial_events=initial_events,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/v1/runs/{run_id}/tasks/{task_id}",
        response_model=ProductTaskDetail,
    )
    async def get_task(run_id: UUID, task_id: str) -> ProductTaskDetail:
        try:
            return await service.get_task(run_id, task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/runs/{run_id}/tasks/{task_id}/diff",
        response_model=ProductTaskDiff,
    )
    async def get_task_diff(
        request: Request,
        run_id: UUID,
        task_id: str,
        kind: Annotated[ProductDiffKind, Query()] = ProductDiffKind.TASK,
    ) -> ProductTaskDiff:
        unexpected = sorted(set(request.query_params.keys()) - {"kind"})
        if unexpected or len(request.query_params.getlist("kind")) > 1:
            raise HTTPException(
                status_code=400,
                detail="diff requests accept only one optional 'kind' selector",
            )
        try:
            return await service.get_task_diff(run_id, task_id, kind=kind)
        except ProductDiffUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProductWorkspaceNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted Git diff evidence failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app
