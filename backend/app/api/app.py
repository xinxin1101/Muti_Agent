from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse

from app.api.github_publication import (
    ProductGitHubPublicationConfigurationError,
    ProductGitHubPublicationFailedError,
)
from app.api.models import (
    ProductDependencyCacheCleanup,
    ProductDependencyEnvironmentMetrics,
    ProductDependencyEnvironmentStatus,
    ProductDiffKind,
    ProductGitHubPublication,
    ProductProject,
    ProductProjectDeletionPreview,
    ProductProjectDeletionResult,
    ProductRun,
    ProductRunDAG,
    ProductRunDetail,
    ProductRunMetrics,
    ProductTaskDetail,
    ProductTaskDiff,
    ProjectCreateRequest,
    ProjectDeleteRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.api.publication import ProductGitHubPublicationUnavailableError
from app.api.service import (
    ProductDependencyEnvironmentUnavailableError,
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
from app.persistence import (
    PersistenceConflictError,
    PersistenceCorruptionError,
    PersistenceDAGUnavailableError,
)
from app.verification.dependency_preflight import DependencyEnvironmentPreflightError
from app.workspace import ProjectProvisionError, WorkspaceGitError
from app.workspace.lifecycle import LocalProjectCleanupError

StartupCheck = Callable[[], Awaitable[None]]


def create_app(
    service: ProductRuntimeService,
    *,
    close_service: bool = False,
    startup_check: StartupCheck | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            if startup_check is not None:
                await startup_check()
            start_recovery_monitor = getattr(service, "start_recovery_monitor", None)
            if callable(start_recovery_monitor):
                await start_recovery_monitor()
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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        fingerprint = getattr(app.state, "runtime_fingerprint", "unknown")
        return {"status": "ok", "runtime_fingerprint": fingerprint}

    @app.get("/api/v1/projects", response_model=list[ProductProject])
    async def list_projects(
        include_archived: Annotated[bool, Query()] = False,
    ) -> tuple[ProductProject, ...]:
        if include_archived:
            return await service.list_projects(include_archived=True)
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

    @app.post("/api/v1/projects/{project_id}/archive", response_model=ProductProject)
    async def archive_project(project_id: UUID) -> ProductProject:
        try:
            return await service.archive_project(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/projects/{project_id}/restore", response_model=ProductProject)
    async def restore_project(project_id: UUID) -> ProductProject:
        try:
            return await service.restore_project(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/projects/{project_id}/deletion-preview",
        response_model=ProductProjectDeletionPreview,
    )
    async def project_deletion_preview(project_id: UUID) -> ProductProjectDeletionPreview:
        try:
            return await service.project_deletion_preview(project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete(
        "/api/v1/projects/{project_id}",
        response_model=ProductProjectDeletionResult,
    )
    async def delete_project(
        project_id: UUID,
        request: ProjectDeleteRequest,
    ) -> ProductProjectDeletionResult:
        try:
            return await service.delete_project(project_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LocalProjectCleanupError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/runs", response_model=list[ProductRun])
    async def list_runs(
        project_id: Annotated[UUID | None, Query()] = None,
        include_archived: Annotated[bool, Query()] = False,
    ) -> tuple[ProductRun, ...]:
        try:
            if include_archived:
                return await service.list_runs(
                    project_id=project_id,
                    include_archived=True,
                )
            return await service.list_runs(project_id=project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/runs/{run_id}/archive", status_code=status.HTTP_204_NO_CONTENT)
    async def archive_run(run_id: UUID) -> None:
        try:
            await service.archive_run(run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs",
        response_model=RunLaunchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_run(request: RunCreateRequest) -> RunLaunchResponse:
        try:
            return await service.create_run(request)
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except ProductWorkspaceNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except WorkspaceGitError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/api/v1/projects/{project_id}/dependency-environment",
        response_model=ProductDependencyEnvironmentStatus,
    )
    async def get_dependency_environment(project_id: UUID) -> ProductDependencyEnvironmentStatus:
        try:
            return await service.get_dependency_environment(project_id)
        except ProductDependencyEnvironmentUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except (ProductWorkspaceNotReadyError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/projects/{project_id}/dependency-environment/rebuild",
        response_model=ProductDependencyEnvironmentStatus,
    )
    async def rebuild_dependency_environment(
        request: Request,
        project_id: UUID,
    ) -> ProductDependencyEnvironmentStatus:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail="dependency environment rebuild accepts no body",
            )
        try:
            return await service.rebuild_dependency_environment(project_id)
        except ProductDependencyEnvironmentUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except DependencyEnvironmentPreflightError as exc:
            raise HTTPException(status_code=424, detail=exc.public_detail) from exc
        except (ProductWorkspaceNotReadyError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/v1/dependency-environment/metrics",
        response_model=ProductDependencyEnvironmentMetrics,
    )
    async def dependency_environment_metrics(
        request: Request,
    ) -> ProductDependencyEnvironmentMetrics:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="dependency environment metrics accepts no query",
            )
        try:
            return await service.dependency_environment_metrics()
        except ProductDependencyEnvironmentUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post(
        "/api/v1/dependency-environment/cleanup",
        response_model=ProductDependencyCacheCleanup,
    )
    async def cleanup_dependency_environments(request: Request) -> ProductDependencyCacheCleanup:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(status_code=400, detail="dependency cache cleanup accepts no body")
        try:
            return await service.cleanup_dependency_environments()
        except ProductDependencyEnvironmentUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

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

    @app.get(
        "/api/v1/runs/{run_id}/github-publication",
        response_model=ProductGitHubPublication,
    )
    async def get_github_publication(
        request: Request,
        run_id: UUID,
    ) -> ProductGitHubPublication:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="GitHub publication does not accept browser-authored selectors",
            )
        try:
            return await service.get_github_publication(run_id)  # type: ignore[attr-defined]
        except ProductGitHubPublicationUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="GitHub publication source facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/github-publication",
        response_model=ProductGitHubPublication,
    )
    async def publish_github_draft(
        request: Request,
        run_id: UUID,
    ) -> ProductGitHubPublication:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="GitHub publication does not accept browser-authored selectors",
            )
        if (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail="GitHub publication does not accept a browser-authored request body",
            )
        try:
            return await service.publish_github_draft(run_id)  # type: ignore[attr-defined]
        except ProductGitHubPublicationUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ProductGitHubPublicationConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProductGitHubPublicationFailedError as exc:
            status_code = 409 if exc.code == "REMOTE_BRANCH_CONFLICT" else 502
            raise HTTPException(status_code=status_code, detail=exc.public_message) from exc
        except PersistenceConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="GitHub publication audit/source facts failed integrity validation",
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
