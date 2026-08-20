from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.models import (
    CheckResult,
    CheckType,
    DeveloperRunResult,
    DeveloperStopReason,
    FailureReport,
    FailureSource,
    FailureType,
    RepairRunResult,
    RepairStopReason,
    ReviewDecision,
    ReviewOutcome,
    RunEvent,
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventSource,
    SingleTaskRunResult,
    TaskContract,
    TaskLeaseState,
    TaskRunState,
    VerificationResult,
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import (
    PersistenceConflictError,
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


async def _new_run(
    store: PostgresEvidenceStore,
    task_id: str,
) -> tuple[object, TaskContract]:
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


def test_runtime_event_draft_rejects_oversized_attributes() -> None:
    with pytest.raises(ValidationError, match="exceed"):
        RuntimeEventDraft(
            event_key="too-large",
            kind=RuntimeEventKind.EVIDENCE_RECORDED,
            source=RuntimeEventSource.RUNTIME,
            message="Keep event metadata bounded.",
            attributes={"blob": "x" * 17_000},
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


def test_evidence_kinds_project_to_distinct_runtime_sources() -> None:
    asyncio.run(_evidence_source_projection())


async def _evidence_source_projection() -> None:
    store = PostgresEvidenceStore.from_url(_database_url())
    run_id, task = await _new_run(store, "EVENT-SOURCES")
    dispatch_id = uuid4()

    developer = DeveloperRunResult(
        stop_reason=DeveloperStopReason.MODEL_STOP,
        iterations=1,
        tool_calls=0,
    )
    verification = VerificationResult(
        passed=True,
        checks=[
            CheckResult(
                check_type=CheckType.TEST,
                name="pytest",
                passed=True,
                exit_code=0,
            )
        ],
    )
    review = ReviewDecision(decision=ReviewOutcome.PASS, summary="Accepted.")
    repair = RepairRunResult(
        attempt=1,
        failure_types=[FailureType.TEST_FAILURE],
        stop_reason=RepairStopReason.MODEL_STOP,
        iterations=1,
        tool_calls=0,
    )
    dispatch = WorkerDispatchEvent(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task.task_id,
        phase=WorkerDispatchPhase.RECEIVED,
    )
    failure = FailureReport(
        failure_type=FailureType.TOOL_FAILURE,
        source=FailureSource.RUNTIME,
        message="Worker execution failed for source-mapping coverage.",
        retryable=False,
    )
    worker = WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task.task_id,
        status=WorkerExecutionStatus.FAILED,
        base_commit="a" * 40,
        failures=(failure,),
        duration_ms=1,
    )

    fixtures = (
        (
            "developer:source",
            PersistenceEvidenceKind.DEVELOPER_RUN,
            developer,
            RuntimeEventSource.AGENT,
        ),
        (
            "verification:source",
            PersistenceEvidenceKind.VERIFICATION_RESULT,
            verification,
            RuntimeEventSource.VERIFICATION,
        ),
        (
            "review:source",
            PersistenceEvidenceKind.REVIEW_DECISION,
            review,
            RuntimeEventSource.REVIEW,
        ),
        (
            "repair:source",
            PersistenceEvidenceKind.REPAIR_RUN,
            repair,
            RuntimeEventSource.REPAIR,
        ),
        (
            "dispatch:source",
            PersistenceEvidenceKind.DISPATCH_EVENT,
            dispatch,
            RuntimeEventSource.DISPATCH,
        ),
        (
            "worker:source",
            PersistenceEvidenceKind.WORKER_EXECUTION,
            worker,
            RuntimeEventSource.WORKER,
        ),
    )

    for sequence, (key, kind, payload, _) in enumerate(fixtures):
        await store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key=key,
            kind=kind,
            payload_model=payload,
            stage="source-test",
            sequence=sequence,
        )

    evidence_events = await store.list_runtime_events(
        run_id,
        task_id=task.task_id,
        kind=RuntimeEventKind.EVIDENCE_RECORDED,
    )
    assert [event.source for event in evidence_events] == [
        expected_source for _, _, _, expected_source in fixtures
    ]
    assert [event.attributes["evidence_kind"] for event in evidence_events] == [
        kind.value for _, kind, _, _ in fixtures
    ]
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
        lease_seconds=5.0,
    )
    await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_a,
        run_token=first.run_token,
        lease_seconds=0.2,
    )
    # Keep acquisition comfortably live for the heartbeat, then intentionally expire only the
    # renewed short lease before exercising takeover. This avoids CI/database scheduling races
    # without weakening the production rule that expired generations cannot be resurrected.
    await asyncio.sleep(0.3)

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


def test_terminal_run_allows_lease_release_event_but_keeps_evidence_append_closed() -> None:
    asyncio.run(_terminal_cleanup_event())


async def _terminal_cleanup_event() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, task = await _new_run(evidence_store, "EVENT-TERMINAL-CLEANUP")
    dispatch_id = uuid4()

    grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    await evidence_store.finalize_single_task_run(
        run_id=run_id,
        result=_success_result(task.task_id),
        run_token=grant.run_token,
    )

    with pytest.raises(PersistenceConflictError, match="append-closed"):
        await evidence_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="late:forbidden",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=RunEvent(
                sequence=99,
                state=TaskRunState.SUCCEEDED,
                detail="Late typed evidence must stay rejected.",
            ),
            stage="runtime",
            sequence=99,
            run_token=grant.run_token,
        )

    released = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
    )
    assert released.state is TaskLeaseState.RELEASED

    events = await evidence_store.list_runtime_events(run_id)
    assert [event.kind for event in events] == [
        RuntimeEventKind.RUN_STARTED,
        RuntimeEventKind.LEASE_ACQUIRED,
        RuntimeEventKind.RUN_FINALIZED,
        RuntimeEventKind.LEASE_RELEASED,
    ]
    assert events[-1].sequence == events[-2].sequence + 1
    assert events[-1].dispatch_id == dispatch_id
    assert events[-1].generation == grant.snapshot.generation

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