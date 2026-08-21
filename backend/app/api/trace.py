from __future__ import annotations

from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request

from app.api.autonomous import AutonomousProductRuntimeService
from app.models.dispatch_attempt import PersistedDispatchAttempt
from app.models.trace import CausalRunTrace
from app.persistence.errors import PersistenceCorruptionError
from app.trace.projector import CausalTraceProjector, TraceProjectionUnavailableError


class ProductTraceDispatchReader(Protocol):
    async def list_for_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]: ...


class TraceableAutonomousProductRuntimeService(AutonomousProductRuntimeService):
    """Autonomous Product facade plus one read-only diagnostic trace projection."""

    def __init__(
        self,
        *,
        trace_dispatch_reader: ProductTraceDispatchReader,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._trace_projector = CausalTraceProjector(
            evidence_reader=self._evidence_store,  # type: ignore[arg-type]
            dispatch_reader=trace_dispatch_reader,
        )

    async def get_run_trace(self, run_id: UUID) -> CausalRunTrace:
        return await self._trace_projector.project(run_id)


def attach_trace_routes(
    app: FastAPI,
    service: TraceableAutonomousProductRuntimeService,
) -> None:
    @app.get("/api/v1/runs/{run_id}/trace", response_model=CausalRunTrace)
    async def get_run_trace(request: Request, run_id: UUID) -> CausalRunTrace:
        if request.query_params:
            raise HTTPException(
                status_code=400,
                detail="Causal Trace does not accept browser-authored selectors",
            )
        try:
            return await service.get_run_trace(run_id)
        except TraceProjectionUnavailableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PersistenceCorruptionError as exc:
            raise HTTPException(
                status_code=500,
                detail="persisted Causal Trace source facts failed integrity validation",
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
