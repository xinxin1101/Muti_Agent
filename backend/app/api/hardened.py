from __future__ import annotations

import asyncio
from uuid import UUID

from app.api.autonomous import (
    InitialTaskDispatch,
    ProductPlannerUnavailableError,
    RequirementDispatchState,
    RequirementRunCreateRequest,
    RequirementRunLaunchResponse,
    RequirementRunLaunchState,
)
from app.api.models import (
    DispatchStatus,
    ProductProject,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.api.operator import OperatorAwareAutonomousProductRuntimeService
from app.api.repository_context import RepositoryPlanningContextBuilder
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG, TaskNode
from app.workspace import ProjectProvisionError, WorkspaceGitError


class HardenedOperatorAwareAutonomousProductRuntimeService(
    OperatorAwareAutonomousProductRuntimeService
):
    """Real-world Product entry points over the already-accepted runtime authority layers."""

    def __init__(self, *, planning_context_builder: RepositoryPlanningContextBuilder, **kwargs) -> None:
        super().__init__(**kwargs)
        self._planning_context_builder = planning_context_builder

    async def list_projects(self) -> tuple[ProductProject, ...]:
        projects = await self._catalog.list_projects()
        return tuple(await asyncio.gather(*(self._strong_project_state(item) for item in projects)))

    async def get_project(self, project_id: UUID) -> ProductProject:
        return await self._strong_project_state(await self._catalog.get_project(project_id))

    async def create_project(self, request: ProjectCreateRequest) -> ProductProject:
        project_id = await self._evidence_store.ensure_project(
            repository_url=str(request.repository_url),
            default_branch=request.default_branch,
        )
        await self._project_store_call("mark_project_provisioning", project_id)
        try:
            await asyncio.to_thread(
                self._provisioner.provision,
                project_id,
                repository_url=str(request.repository_url),
                default_branch=request.default_branch,
            )
            commit = await asyncio.to_thread(
                self._provisioner.synchronize,  # type: ignore[attr-defined]
                project_id,
                repository_url=str(request.repository_url),
                default_branch=request.default_branch,
            )
            await self._project_store_call(
                "mark_project_ready",
                project_id,
                synced_commit=commit,
            )
        except (ProjectProvisionError, WorkspaceGitError, OSError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__.upper())
            await self._project_store_call(
                "mark_project_failed",
                project_id,
                code=str(code)[:64],
                message=str(exc)[:512] or "Project provisioning failed.",
            )
            raise
        return await self.get_project(project_id)

    async def create_run(self, request: RunCreateRequest) -> RunLaunchResponse:
        project = await self._catalog.get_project(request.project_id)
        base_commit = await self._synchronize_project(project)
        dag = TaskDAG(tasks=(TaskNode(task=request.task, depends_on=()),))
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        try:
            receipt = await self._dispatcher.dispatch(
                run_id=run_id,
                task_id=request.task.task_id,
            )
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

    async def create_requirement_run(
        self,
        request: RequirementRunCreateRequest,
    ) -> RequirementRunLaunchResponse:
        if self._requirement_planner is None:
            raise ProductPlannerUnavailableError(
                "natural-language planning is unavailable because the Planner provider is not "
                "configured"
            )
        project = await self._catalog.get_project(request.project_id)
        base_commit = await self._synchronize_project(project)
        try:
            workspace = self._workspace_resolver.resolve(request.project_id)
            repository_context = await asyncio.to_thread(
                self._planning_context_builder.build,
                workspace,
                base_commit=base_commit,
                requirement=request.requirement,
                repository_url=project.repository_url,
                default_branch=project.default_branch,
            )
        except (ValueError, WorkspaceGitError) as exc:
            raise ProjectProvisionError(
                "frozen repository planning context is unavailable",
                code="PLANNING_CONTEXT_UNAVAILABLE",
            ) from exc

        dag = await self._requirement_planner.plan(
            request.requirement,
            repository_context=repository_context,
        )
        dag = TaskDAG.model_validate(dag.model_dump(mode="python"))
        run_id = await self._dag_store.start_run(
            project_id=request.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        persisted_dag = await self._dag_store.load_dag(run_id)
        initial_ready = tuple(
            dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set())
        )
        if not initial_ready:
            raise RuntimeError("validated TaskDAG unexpectedly has no initial READY task")

        dispatches: list[InitialTaskDispatch] = []
        for task_id in initial_ready:
            dispatches.append(await self._dispatch_initial_task(run_id=run_id, task_id=task_id))
        queued = sum(item.state is RequirementDispatchState.QUEUED for item in dispatches)
        if queued == len(dispatches):
            launch_state = RequirementRunLaunchState.QUEUED
        elif queued == 0:
            launch_state = RequirementRunLaunchState.BROKER_UNAVAILABLE
        else:
            launch_state = RequirementRunLaunchState.PARTIAL
        return RequirementRunLaunchResponse(
            run_id=run_id,
            project_id=request.project_id,
            base_commit=base_commit,
            dag_sha256=persisted_dag.dag_sha256,
            task_ids=tuple(dag.topological_order()),
            initial_ready_task_ids=initial_ready,
            launch_state=launch_state,
            dispatches=tuple(dispatches),
        )

    async def _synchronize_project(self, project: ProductProject) -> str:
        state = await asyncio.to_thread(
            self._provisioner.readiness,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        if not state.ready:
            raise ProjectProvisionError(state.detail, code="WORKSPACE_NOT_READY")
        commit = await asyncio.to_thread(
            self._provisioner.synchronize,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        await self._project_store_call(
            "record_project_sync",
            project.project_id,
            commit=commit,
        )
        return commit

    async def _strong_project_state(self, project: ProductProject) -> ProductProject:
        readiness = await asyncio.to_thread(
            self._provisioner.readiness,  # type: ignore[attr-defined]
            project.project_id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
        )
        return project.model_copy(update={"workspace_ready": readiness.ready})

    async def _project_store_call(self, method_name: str, *args, **kwargs) -> None:
        method = getattr(self._evidence_store, method_name, None)
        if method is None:
            raise RuntimeError(f"Project lifecycle store is missing {method_name}")
        await method(*args, **kwargs)
