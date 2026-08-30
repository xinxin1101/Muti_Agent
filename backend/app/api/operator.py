from __future__ import annotations

import asyncio
import re
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request

from app.api.autonomous import (
    RequirementDispatchState,
    RequirementRunLaunchResponse,
    RequirementRunLaunchState,
)
from app.api.trace import TraceableAutonomousProductRuntimeService
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dag import TaskDAG
from app.models.operator_recovery import (
    OperatorActionExecutionResult,
    OperatorRecoveryPlan,
)
from app.models.run_reconciliation import DAGTaskFrontierState
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.types import PersistedRunStatus
from app.runtime.merge_queue import MergeQueueError
from app.runtime.operator_recovery import OperatorActionStaleError, OperatorRecoveryCoordinator
from app.workspace import ProjectProvisionError, WorkspaceGitError

_ACTION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class OperatorAuditResource(Protocol):
    async def dispose(self) -> None: ...


class OperatorAwareAutonomousProductRuntimeService(TraceableAutonomousProductRuntimeService):
    """Product facade with one bounded operator recovery request surface."""

    def __init__(
        self,
        *,
        operator_recovery: OperatorRecoveryCoordinator,
        operator_audit_resource: OperatorAuditResource,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._operator_recovery = operator_recovery
        self._operator_audit_resource = operator_audit_resource

    async def dispose(self) -> None:
        try:
            await super().dispose()
        finally:
            await self._operator_audit_resource.dispose()

    async def get_operator_recovery_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        return await self._operator_recovery.get_plan(run_id)

    async def execute_operator_action(
        self,
        *,
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
        return await self._operator_recovery.execute(run_id=run_id, action_id=action_id)

    async def recover_interrupted_run(self, run_id: UUID) -> RequirementRunLaunchResponse:
        """Create a new Run only for a durably diagnosed released-evidence gap.

        The interrupted Run remains immutable. This is deliberately not a lease reacquisition and
        never retries a possibly completed generation in-place.
        """

        previous = await self._evidence_store.load_run(run_id)
        plan = await self._operator_recovery.get_plan(run_id)
        if previous.status is not PersistedRunStatus.RUNNING or not any(
            item.frontier_state is DAGTaskFrontierState.BLOCKED_RECOVERY_GAP
            for item in plan.reconciliation.tasks
        ):
            raise PersistenceConflictError(
                "new recovery Runs are available only for a released execution "
                "without terminal evidence"
            )

        project = await self._catalog.get_project(previous.project_id)
        base_commit = await self._retry_base_commit(project)
        persisted_dag = await self._dag_store.load_dag(run_id)
        dag = TaskDAG.model_validate(persisted_dag.dag.model_dump(mode="python"))
        new_run_id = await self._dag_store.start_run(
            project_id=previous.project_id,
            dag=dag,
            base_commit=base_commit,
        )
        await self._initialize_run_token_budget(new_run_id)
        new_persisted_dag = await self._dag_store.load_dag(new_run_id)
        initial_ready = tuple(dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()))
        if not initial_ready:
            raise PersistenceCorruptionError("persisted TaskDAG has no initial READY task")
        dispatches = tuple(
            await asyncio.gather(
                *(
                    self._dispatch_initial_task(run_id=new_run_id, task_id=task_id)
                    for task_id in initial_ready
                )
            )
        )
        queued = sum(item.state is RequirementDispatchState.QUEUED for item in dispatches)
        launch_state = (
            RequirementRunLaunchState.QUEUED
            if queued == len(dispatches)
            else RequirementRunLaunchState.BROKER_UNAVAILABLE
            if queued == 0
            else RequirementRunLaunchState.PARTIAL
        )
        return RequirementRunLaunchResponse(
            run_id=new_run_id,
            project_id=previous.project_id,
            base_commit=base_commit,
            dag_sha256=new_persisted_dag.dag_sha256,
            task_ids=tuple(dag.topological_order()),
            initial_ready_task_ids=initial_ready,
            launch_state=launch_state,
            dispatches=dispatches,
        )


def attach_operator_routes(
    app: FastAPI,
    service: OperatorAwareAutonomousProductRuntimeService,
) -> None:
    @app.get(
        "/api/v1/runs/{run_id}/operator-recovery",
        response_model=OperatorRecoveryPlan,
    )
    async def get_operator_recovery(request: Request, run_id: UUID) -> OperatorRecoveryPlan:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="operator recovery does not accept browser-authored authority selectors",
            )
        try:
            return await service.get_operator_recovery_plan(run_id)
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted operator recovery source facts failed integrity validation",
            ) from exc
        except (ValueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/operator-actions/{action_id}",
        response_model=OperatorActionExecutionResult,
    )
    async def execute_operator_action(
        request: Request,
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
        if request.query_params or await request.body():
            raise HTTPException(
                status_code=400,
                detail="operator action accepts only the server-issued action id",
            )
        if _ACTION_ID_RE.fullmatch(action_id) is None:
            raise HTTPException(status_code=404, detail="operator action does not exist")
        try:
            return await service.execute_operator_action(run_id=run_id, action_id=action_id)
        except OperatorActionStaleError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PersistenceConflictError, MergeQueueError, WorkspaceGitError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except TaskDispatchBrokerError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="operator action failed durable integrity revalidation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/api/v1/runs/{run_id}/recover-as-new",
        response_model=RequirementRunLaunchResponse,
        status_code=201,
    )
    async def recover_interrupted_run(
        request: Request,
        run_id: UUID,
    ) -> RequirementRunLaunchResponse:
        if request.query_params or (await request.body()).strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "interrupted-run recovery does not accept browser-authored "
                    "task or Git authority"
                ),
            )
        try:
            return await service.recover_interrupted_run(run_id)
        except (
            PersistenceConflictError,
            ProjectProvisionError,
            WorkspaceGitError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="interrupted-run recovery facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
