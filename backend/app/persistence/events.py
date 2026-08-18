from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.models import RunRow, RuntimeEventRow
from app.persistence.serialization import payload_sha256, verify_payload_hash

_EVENT_SCHEMA_VERSION = 1


async def append_runtime_event(
    session: AsyncSession,
    *,
    run: RunRow,
    draft: RuntimeEventDraft,
) -> PersistedRuntimeEvent:
    """Append one idempotent event while the caller owns the Run row lock."""

    attributes_sha256 = payload_sha256(draft.attributes)
    existing = (
        await session.execute(
            select(RuntimeEventRow).where(
                RuntimeEventRow.run_id == run.id,
                RuntimeEventRow.event_key == draft.event_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if _same_event(existing, draft=draft, attributes_sha256=attributes_sha256):
            return decode_runtime_event(existing)
        raise PersistenceConflictError(
            f"runtime event key {draft.event_key!r} was reused for different event data"
        )

    run.event_sequence += 1
    row = RuntimeEventRow(
        event_id=uuid4(),
        run_id=run.id,
        sequence=run.event_sequence,
        event_key=draft.event_key,
        kind=draft.kind.value,
        source=draft.source.value,
        level=draft.level.value,
        task_id=draft.task_id,
        dispatch_id=draft.dispatch_id,
        generation=draft.generation,
        message=draft.message,
        schema_version=_EVENT_SCHEMA_VERSION,
        attributes=draft.attributes,
        attributes_sha256=attributes_sha256,
    )
    session.add(row)
    await session.flush()
    return decode_runtime_event(row)


def decode_runtime_event(row: RuntimeEventRow) -> PersistedRuntimeEvent:
    if row.schema_version != _EVENT_SCHEMA_VERSION:
        raise PersistenceCorruptionError(
            f"unsupported runtime event schema version {row.schema_version} for row {row.id}"
        )
    verify_payload_hash(
        row.attributes,
        row.attributes_sha256,
        label=f"runtime event {row.id} attributes",
    )
    try:
        kind = RuntimeEventKind(row.kind)
        source = RuntimeEventSource(row.source)
        level = RuntimeEventLevel(row.level)
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted runtime event {row.id} contains an unknown enum value"
        ) from exc

    try:
        return PersistedRuntimeEvent(
            id=row.id,
            event_id=row.event_id,
            run_id=row.run_id,
            sequence=row.sequence,
            event_key=row.event_key,
            kind=kind,
            source=source,
            level=level,
            task_id=row.task_id,
            dispatch_id=row.dispatch_id,
            generation=row.generation,
            message=row.message,
            schema_version=row.schema_version,
            attributes=row.attributes,
            attributes_sha256=row.attributes_sha256,
            created_at=row.created_at,
        )
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted runtime event {row.id} failed schema validation: {exc}"
        ) from exc


def _same_event(
    row: RuntimeEventRow,
    *,
    draft: RuntimeEventDraft,
    attributes_sha256: str,
) -> bool:
    return (
        row.kind == draft.kind.value
        and row.source == draft.source.value
        and row.level == draft.level.value
        and row.task_id == draft.task_id
        and row.dispatch_id == draft.dispatch_id
        and row.generation == draft.generation
        and row.message == draft.message
        and row.schema_version == _EVENT_SCHEMA_VERSION
        and row.attributes_sha256 == attributes_sha256
    )
