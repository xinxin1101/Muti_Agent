from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest

from app.api import create_app
from app.api.sse import (
    RuntimeEventStreamSafetyError,
    encode_runtime_event,
    resolve_event_cursor,
    runtime_event_stream,
)
from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")


def _event(
    sequence: int,
    *,
    attributes: dict[str, object] | None = None,
) -> PersistedRuntimeEvent:
    return PersistedRuntimeEvent(
        id=sequence,
        event_id=uuid4(),
        run_id=RUN_ID,
        sequence=sequence,
        event_key=f"event:{sequence}",
        kind=RuntimeEventKind.EVIDENCE_RECORDED,
        source=RuntimeEventSource.RUNTIME,
        level=RuntimeEventLevel.INFO,
        task_id="task-1",
        message=f"Accepted event {sequence}.",
        schema_version=1,
        attributes=attributes or {"evidence_id": sequence},
        attributes_sha256="a" * 64,
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def test_resume_cursor_prefers_the_furthest_valid_sequence() -> None:
    assert resolve_event_cursor(after_sequence=7, last_event_id=None) == 7
    assert resolve_event_cursor(after_sequence=7, last_event_id=" 9 ") == 9
    assert resolve_event_cursor(after_sequence=11, last_event_id="9") == 11

    with pytest.raises(ValueError, match="Last-Event-ID"):
        resolve_event_cursor(after_sequence=0, last_event_id="not-a-sequence")


def test_sse_encoding_uses_runtime_sequence_as_event_id() -> None:
    encoded = encode_runtime_event(_event(3))

    assert encoded.startswith("id: 3\ndata: ")
    assert '"sequence":3' in encoded
    assert "run_token" not in encoded
    assert encoded.endswith("\n\n")


def test_sse_encoding_rejects_sensitive_nested_attributes() -> None:
    event = _event(1, attributes={"nested": {"run_token": str(uuid4())}})

    with pytest.raises(RuntimeEventStreamSafetyError, match="browser-safe"):
        encode_runtime_event(event)


class FakeReader:
    def __init__(self, events: tuple[PersistedRuntimeEvent, ...]) -> None:
        self.events = events
        self.calls: list[tuple[int, int]] = []

    async def get_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]:
        assert run_id == RUN_ID
        self.calls.append((after_sequence, limit))
        return tuple(event for event in self.events if event.sequence > after_sequence)[:limit]


class ConnectedProbe:
    async def is_disconnected(self) -> bool:
        return False


def test_runtime_stream_resumes_monotonically_after_initial_batch() -> None:
    async def scenario() -> tuple[str, str, str, list[tuple[int, int]]]:
        reader = FakeReader((_event(4),))
        stream = runtime_event_stream(
            reader,
            ConnectedProbe(),
            run_id=RUN_ID,
            after_sequence=2,
            initial_events=(_event(3),),
            poll_interval_seconds=0.001,
            heartbeat_seconds=60,
        )
        retry = await anext(stream)
        first = await anext(stream)
        second = await anext(stream)
        await stream.aclose()
        return retry, first, second, reader.calls

    retry, first, second, calls = asyncio.run(scenario())

    assert retry == "retry: 2000\n\n"
    assert first.startswith("id: 3\n")
    assert second.startswith("id: 4\n")
    assert calls == [(3, 200)]


class MissingRunService:
    async def get_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ):
        raise ValueError(f"unknown persistence run: {run_id}")

    async def dispose(self) -> None:
        return None


async def _request(path: str, *, headers: dict[str, str] | None = None) -> httpx.Response:
    service = MissingRunService()
    transport = httpx.ASGITransport(app=create_app(service))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, headers=headers)


def test_sse_route_rejects_invalid_resume_header_before_streaming() -> None:
    response = asyncio.run(
        _request(
            f"/api/v1/runs/{RUN_ID}/events",
            headers={"Last-Event-ID": "bad-cursor"},
        )
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Last-Event-ID")


def test_sse_route_maps_unknown_run_to_404_before_streaming() -> None:
    response = asyncio.run(_request(f"/api/v1/runs/{uuid4()}/events"))

    assert response.status_code == 404
