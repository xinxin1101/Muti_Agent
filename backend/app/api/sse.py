from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from time import monotonic
from typing import Protocol
from uuid import UUID

from app.models.events import PersistedRuntimeEvent

SSE_BATCH_LIMIT = 200
SSE_POLL_INTERVAL_SECONDS = 0.75
SSE_HEARTBEAT_SECONDS = 15.0
SSE_RETRY_MILLISECONDS = 2_000

_MAX_SEQUENCE_CURSOR = (2**63) - 1
_MAX_SSE_EVENT_BYTES = 32_768
_SENSITIVE_ATTRIBUTE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "password",
    "refresh_token",
    "run_token",
    "secret",
    "siliconflow_api_key",
}


class RuntimeEventStreamSafetyError(RuntimeError):
    """Raised when an event is unsafe or inconsistent for browser streaming."""


class RuntimeEventReader(Protocol):
    async def get_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = SSE_BATCH_LIMIT,
    ) -> tuple[PersistedRuntimeEvent, ...]: ...


class DisconnectProbe(Protocol):
    async def is_disconnected(self) -> bool: ...


def resolve_event_cursor(*, after_sequence: int, last_event_id: str | None) -> int:
    """Resolve the browser resume cursor without granting it runtime authority."""

    if after_sequence < 0 or after_sequence > _MAX_SEQUENCE_CURSOR:
        raise ValueError("after_sequence is outside the supported sequence range")
    if last_event_id is None or not last_event_id.strip():
        return after_sequence

    raw = last_event_id.strip()
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError("Last-Event-ID must be a non-negative decimal runtime sequence")
    parsed = int(raw)
    if parsed > _MAX_SEQUENCE_CURSOR:
        raise ValueError("Last-Event-ID is outside the supported sequence range")
    return max(after_sequence, parsed)


def validate_runtime_event_batch(
    events: Sequence[PersistedRuntimeEvent],
    *,
    run_id: UUID,
    after_sequence: int,
) -> int:
    """Fail closed on cross-Run or non-monotonic event batches before streaming."""

    cursor = after_sequence
    for event in events:
        if event.run_id != run_id:
            raise RuntimeEventStreamSafetyError("runtime event belongs to a different Run")
        if event.sequence <= cursor:
            raise RuntimeEventStreamSafetyError("runtime event sequence is not strictly monotonic")
        _assert_browser_safe_attributes(event.attributes)
        cursor = event.sequence
    return cursor


def encode_runtime_event(event: PersistedRuntimeEvent) -> str:
    """Encode one immutable runtime event as a browser-safe SSE message."""

    _assert_browser_safe_attributes(event.attributes)
    payload = event.model_dump_json()
    if len(payload.encode("utf-8")) > _MAX_SSE_EVENT_BYTES:
        raise RuntimeEventStreamSafetyError("runtime event exceeds the SSE payload bound")
    return f"id: {event.sequence}\ndata: {payload}\n\n"


async def runtime_event_stream(
    reader: RuntimeEventReader,
    disconnect_probe: DisconnectProbe,
    *,
    run_id: UUID,
    after_sequence: int,
    initial_events: Sequence[PersistedRuntimeEvent] = (),
    batch_limit: int = SSE_BATCH_LIMIT,
    poll_interval_seconds: float = SSE_POLL_INTERVAL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """Project persisted events to SSE without becoming a second runtime truth source."""

    if batch_limit < 1 or batch_limit > 1000:
        raise ValueError("SSE batch_limit must be between 1 and 1000")
    if poll_interval_seconds <= 0:
        raise ValueError("SSE poll interval must be positive")
    if heartbeat_seconds <= 0:
        raise ValueError("SSE heartbeat interval must be positive")

    cursor = after_sequence
    pending = tuple(initial_events)
    validate_runtime_event_batch(pending, run_id=run_id, after_sequence=cursor)
    last_activity = monotonic()

    yield f"retry: {SSE_RETRY_MILLISECONDS}\n\n"

    while True:
        if await disconnect_probe.is_disconnected():
            return

        if pending:
            for event in pending:
                if event.sequence <= cursor:
                    raise RuntimeEventStreamSafetyError(
                        "runtime event stream attempted to replay a non-monotonic event"
                    )
                yield encode_runtime_event(event)
                cursor = event.sequence
                last_activity = monotonic()
            pending = ()
            continue

        pending = await reader.get_runtime_events(
            run_id,
            after_sequence=cursor,
            limit=batch_limit,
        )
        if pending:
            validate_runtime_event_batch(pending, run_id=run_id, after_sequence=cursor)
            continue

        now = monotonic()
        if now - last_activity >= heartbeat_seconds:
            yield f": heartbeat after_sequence={cursor}\n\n"
            last_activity = now

        await asyncio.sleep(poll_interval_seconds)


def _assert_browser_safe_attributes(value: object) -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _SENSITIVE_ATTRIBUTE_KEYS or key.endswith("_api_key"):
                raise RuntimeEventStreamSafetyError(
                    f"runtime event attribute {raw_key!r} is not browser-safe"
                )
            _assert_browser_safe_attributes(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _assert_browser_safe_attributes(nested)
