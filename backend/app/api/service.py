from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.api.models import (
    DispatchStatus,
    ProductEvidenceSummary,
    ProductProject,
    ProductRun,
    ProductRunDetail,
    ProductTaskDetail,
    ProductTaskSummary,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dispatch import TaskDispatchReceipt
from app.persistence.types import PersistedRunSnapshot
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
        provisioner: ProductProvisioner,
        workspace_resolver: ProductWorkspaceResolver,
        dispatcher: ProductDispatcher,
    ) -> None:
        self._catalog = catalog
        self._evidence_store = evidence_store
        self._provisioner = provisioner
        self._workspace_resolver = workspace_resolver
        self._dispatcher = dispatcher

    async def dispose(self) -> None:
        await self._catalog.dispose()
        await self._evidence_store.dispose()

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
