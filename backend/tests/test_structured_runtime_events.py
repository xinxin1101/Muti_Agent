from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.models import (
    RunEvent,
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventSource,
    SingleTaskRunResult,
    TaskContract,
    TaskRunState,
)
from app.persistence import (
    PersistenceCorruptionError,
    PersistenceEvidenceKind,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.models import RuntimeEventRow


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for structured event tests")
    pytest.skip("structured runtime event test requires DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Exercise the structured runtime event timeline.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Accepted runtime facts are queryable as structured events."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _success_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )


async def _new_run(store: PostgresEvidenceStore, task_id: str) -> tuple[object, TaskContract]:
    task = _task(task_id)
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/events.git",
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="a" * 40,
    )
    return run_id, task


def test_runtime_event_draft_rejects_sensitive_nested_attributes() -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        RuntimeEventDraft(
            event_key="unsafe",
            kind=RuntimeEventKind.EVIDENCE_RECORDED,
            source=RuntimeEventSource.RUNTIME,
            message="Do not persist fencing capabilities.",
            attributes={"nested": {"run_token": str(uuid4())}},
        )


def test_run_evidence_and_finalization_project_to_ordered_events() -> None:
    asyncio.run(_run_evidence_projection())


async def _run_evidence_projection() -> None:
    store = PostgresEvidenceStore.from_url(_database_url())
    run_id, task = await _new_run(store, "EVENT-PROJECTION")

    initial = await store.list_runtime_events(run_id)
    assert len(initial) == 1
    assert initial[0].sequence == 1
    assert initial[0].kind is RuntimeEventKind.RUN_STARTED
    assert initial[0].task_id is None

    state = RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created.")
    first_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="state:0000",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=state,
        stage="runtime",
        sequence=0,
    )
    duplicate_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="state:0000",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=state,
        stage="runtime",
        sequence=0,
    )
    assert duplicate_id == first_id

    await store.finalize_single_task_run(
        run_id=run_id,
        result=_success_result(task.task_id),
    )

    events = await store.list_runtime_events(run_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.kind for event in events] == [
        RuntimeEventKind.RUN_STARTED,
        RuntimeEventKind.EVIDENCE_RECORDED,
        RuntimeEventKind.RUN_FINALIZED,
    ]
    assert events[1].source is RuntimeEventSource.RUNTIME
    assert events[1].attributes["evidence_kind"] == "STATE_TRANSITION"
    assert events[2].attributes["status"] == "SUCCEEDED"
    assert "run_token" not in "".join(event.model_dump_json() for event in events)

    after_first = await store.list_runtime_events(run_id, after_sequence=1, limit=10)
    assert [event.sequence for event in after_first] == [2, 3]
    runtime_only = await store.list_runtime_events(
        run_id,
        source=RuntimeEventSource.RUNTIME,
    )
    assert len(runtime_only) == 1
    await store.dispose()


def test_concurrent_evidence_projection_has_unique_monotonic_run_sequence() -> None:
    asyncio.run(_concurrent_projection())


async def _concurrent_projection() -> None:
    database_url = _database_url()
    first = PostgresEvidenceStore.from_url(database_url)
    second = PostgresEvidenceStore.from_url(database_url)
    run_id, task = await _new_run(first, "EVENT-CONCURRENT")

    await asyncio.gather(
        first.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="state:a",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=RunEvent(
                sequence=0,
                state=TaskRunState.PENDING,
                detail="A",
            ),
            stage="runtime",
            sequence=0,
        ),
        second.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="state:b",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=RunEvent(
                sequence=1,
                state=TaskRunState.RUNNING,
                detail="B",
            ),
            stage="runtime",
            sequence=1,
        ),
    )

    events = await first.list_runtime_events(run_id)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert len({event.event_id for event in events}) == 3
    assert len({event.event_key for event in events}) == 3
    await first.dispose()
    await second.dispose()


def test_lease_lifecycle_events_track_generation_without_persisting_token() -> None:
    asyncio.run(_lease_event_timeline())


async def _lease_event_timeline() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, task = await _new_run(evidence_store, "EVENT-LEASE")
    dispatch_a = uuid4()

    first = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_a,
        lease_seconds=0.08,
    )
    await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_a,
        run_token=first.run_token,
        lease_seconds=0.05,
    )
    await asyncio.sleep(0.08)

    dispatch_b = uuid4()
    second = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-b",
        dispatch_id=dispatch_b,
        lease_seconds=1.0,
    )
    await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-b",
        dispatch_id=dispatch_b,
        run_token=second.run_token,
    )

    events = await evidence_store.list_runtime_events(run_id, task_id=task.task_id)
    assert [event.kind for event in events] == [
        RuntimeEventKind.LEASE_ACQUIRED,
        RuntimeEventKind.LEASE_HEARTBEAT,
        RuntimeEventKind.LEASE_TAKEN_OVER,
        RuntimeEventKind.LEASE_RELEASED,
    ]
    assert [event.generation for event in events] == [1, 1, 2, 2]
    assert events[0].dispatch_id == dispatch_a
    assert events[-1].dispatch_id == dispatch_b
    assert events[2].attributes["previous_generation"] == 1
    serialized = "".join(event.model_dump_json() for event in events)
    assert str(first.run_token) not in serialized
    assert str(second.run_token) not in serialized
    assert "run_token" not in serialized

    dispatch_events = await evidence_store.list_runtime_events(
        run_id,
        dispatch_id=dispatch_b,
    )
    assert [event.kind for event in dispatch_events] == [
        RuntimeEventKind.LEASE_TAKEN_OVER,
        RuntimeEventKind.LEASE_RELEASED,
    ]
    await lease_store.dispose()
    await evidence_store.dispose()


def test_runtime_event_attribute_hash_corruption_is_detected() -> None:
    asyncio.run(_event_hash_corruption())


async def _event_hash_corruption() -> None:
    database_url = _database_url()
    store = PostgresEvidenceStore.from_url(database_url)
    run_id, _ = await _new_run(store, "EVENT-CORRUPT")

    engine = create_postgres_engine(database_url)
    sessions = create_session_factory(engine)
    async with sessions.begin() as session:
        await session.execute(
            update(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id, RuntimeEventRow.sequence == 1)
            .values(attributes={"tampered": True})
        )
    await engine.dispose()

    with pytest.raises(PersistenceCorruptionError, match="hash mismatch"):
        await store.list_runtime_events(run_id)
    await store.dispose()
