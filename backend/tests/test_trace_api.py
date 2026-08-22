from datetime import UTC, datetime
from uuid import uuid4, uuid5

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.trace import attach_trace_routes
from app.models.trace import (
    CausalRunTrace,
    CausalTraceSpan,
    TraceSpanKind,
    TraceSpanSource,
    TraceSpanStatus,
)


class _TraceService:
    def __init__(self, trace: CausalRunTrace) -> None:
        self.trace = trace
        self.calls = 0

    async def get_run_trace(self, run_id):
        self.calls += 1
        if run_id != self.trace.run_id:
            raise ValueError("unknown run")
        return self.trace


def _trace() -> CausalRunTrace:
    run_id = uuid4()
    root_span_id = uuid5(run_id, "run")
    return CausalRunTrace(
        run_id=run_id,
        root_span_id=root_span_id,
        spans=(
            CausalTraceSpan(
                span_id=root_span_id,
                run_id=run_id,
                kind=TraceSpanKind.RUN,
                status=TraceSpanStatus.UNKNOWN,
                name="run",
                sequence=1,
                occurred_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
                source=TraceSpanSource.PERSISTED_RUN,
                source_record_id=f"run:{run_id}",
            ),
        ),
    )


def test_trace_api_returns_fixed_metadata_only_projection() -> None:
    trace = _trace()
    service = _TraceService(trace)
    app = FastAPI()
    attach_trace_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.get(f"/api/v1/runs/{trace.run_id}/trace")

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostic_only"] is True
    assert body["privacy_mode"] == "METADATA_ONLY"
    assert body["spans"][0]["kind"] == "RUN"
    assert service.calls == 1


def test_trace_api_rejects_browser_authored_selectors_before_projection() -> None:
    trace = _trace()
    service = _TraceService(trace)
    app = FastAPI()
    attach_trace_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.get(
        f"/api/v1/runs/{trace.run_id}/trace",
        params={"dispatch_id": str(uuid4())},
    )

    assert response.status_code == 400
    assert service.calls == 0
