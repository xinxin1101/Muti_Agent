from __future__ import annotations

import re
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request

from app.api.trace import TraceableAutonomousProductRuntimeService
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.operator_recovery import (
    OperatorActionExecutionResult,
    OperatorRecoveryPlan,
)
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.runtime.merge_queue import MergeQueueError
from app.runtime.operator_recovery import OperatorActionStaleError, OperatorRecoveryCoordinator
from app.workspace import WorkspaceGitError

_ACTION_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class OperatorAwareAutonomousProductRuntimeService(TraceableAutonomousProductRuntimeService):
    """Product facade with one bounded operator recovery request surface."""

    def __init__(
        self,
        *,
        operator_recovery: OperatorRecoveryCoordinator,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._operator_recovery = operator_recovery

    async def get_operator_recovery_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        return await self._operator_recovery.get_plan(run_id)

    async def execute_operator_action(
        self,
        *,
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
        return await self._operator_recovery.execute(run_id=run_id, action_id=action_id)


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
        run_id: UUID,
        action_id: str,
    ) -> OperatorActionExecutionResult:
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
