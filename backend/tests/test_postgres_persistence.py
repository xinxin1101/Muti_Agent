from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.models import (
    CheckResult,
    CheckType,
    ContextUsage,
    DeveloperRunResult,
    DeveloperStopReason,
    MergeQueueSnapshot,
    ReviewDecision,
    ReviewOutcome,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskRunState,
    VerificationBackend,
    VerificationResult,
)
from app.persistence import (
    ContextFingerprintReference,
    PersistenceConflictError,
    PersistenceCorruptionError,
    PersistenceEvidenceKind,
    PostgresEvidenceStore,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.models import EvidenceRow
from app.persistence.serialization import decode_terminal_result


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for PostgreSQL persistence tests")
    pytest.skip("PostgreSQL persistence test requires DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Update src/service.py with validated behavior.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["The service behavior is verified."],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=2,
    )


def _success_result(task_id: str) -> SingleTaskRunResult:
    events = [
        RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Task run created."),
        RunEvent(
            sequence=1,
            state=TaskRunState.RUNNING,
            detail="Developer Agent started initial work.",
        ),
        RunEvent(
            sequence=2,
            state=TaskRunState.VERIFYING,
            detail="Deterministic hard gate started.",
        ),
        RunEvent(
            sequence=3,
            state=TaskRunState.REVIEWING,
            detail="Hard gate passed; independent semantic review started.",
        ),
        RunEvent(
            sequence=4,
            state=TaskRunState.SUCCEEDED,
            detail="Hard evidence and independent semantic review both passed.",
        ),
    ]
    developer = DeveloperRunResult(
        stop_reason=DeveloperStopReason.MODEL_STOP,
        iterations=1,
        tool_calls=1,
        final_message="Implemented requested change.",
        changed_files=["src/service.py"],
        latency_ms=15,
    )
    verification = VerificationResult(
        passed=True,
        checks=[
            CheckResult(
                check_type=CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=True,
                exit_code=0,
                stdout="1 passed",
                duration_ms=20,
                execution_backend=VerificationBackend.DOCKER,
                execution_details=("network=none",),
            )
        ],
    )
    review = ReviewDecision(
        decision=ReviewOutcome.PASS,
        summary="The implementation satisfies the task contract.",
        issues=[],
    )
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=events,
        developer=developer,
        verifications=[verification],
        reviews=[review],
        repairs=[],
        failures=[],
        changed_files=["src/service.py"],
        repair_attempts=0,
    )


def _context_reference(task_id: str) -> ContextFingerprintReference:
    return ContextFingerprintReference(
        task_id=task_id,
        stage="developer",
        fingerprint="d" * 64,
        repository_head="a" * 40,
        changed_files=(),
        selection_strategy="python_ast_import_relevance_v1",
        snippet_strategy="python_ast_symbol_regions_v1+deterministic_prefix_fallback",
        token_estimator="utf8_bytes_upper_bound",
        usage=ContextUsage(
            candidate_files=2,
            selected_files=1,
            selected_chars=120,
            estimated_tokens=120,
            truncated_files=0,
            omitted_files=1,
        ),
    )


def test_postgres_full_result_round_trip_and_terminal_append_close() -> None:
    asyncio.run(_full_result_round_trip())


async def _full_result_round_trip() -> None:
    database_url = _database_url()
    task = _task("PG-FULL")
    result = _success_result(task.task_id)
    repository_url = f"https://example.test/{uuid4()}/repo.git"

    store = PostgresEvidenceStore.from_url(database_url)
    project_id = await store.ensure_project(
        repository_url=repository_url,
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="a" * 40,
    )

    first_event_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="state:0000",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=result.events[0],
        stage="runtime",
        sequence=0,
    )
    duplicate_event_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="state:0000",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=result.events[0],
        stage="runtime",
        sequence=0,
    )
    assert duplicate_event_id == first_event_id

    with pytest.raises(PersistenceConflictError, match="reused"):
        await store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="state:0000",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=result.events[1],
            stage="runtime",
            sequence=1,
        )

    await store.record_context_reference(
        run_id=run_id,
        reference=_context_reference(task.task_id),
        evidence_key="context:developer:0000",
        sequence=0,
    )
    await store.record_single_task_result_evidence(run_id=run_id, result=result)
    await store.dispose()

    reopened = PostgresEvidenceStore.from_url(database_url)
    snapshot = await reopened.load_run(run_id)

    assert snapshot.status.value == "SUCCEEDED"
    assert snapshot.tasks[0].task == task
    assert snapshot.terminal_result is not None
    assert decode_terminal_result(snapshot.terminal_result) == result
    assert any(
        item.kind is PersistenceEvidenceKind.CONTEXT_REFERENCE
        and "selected_files" not in item.payload
        for item in snapshot.evidence
    )
    review_evidence = await reopened.list_evidence(
        run_id,
        kind=PersistenceEvidenceKind.REVIEW_DECISION,
    )
    assert len(review_evidence) == 1

    with pytest.raises(PersistenceConflictError, match="append-closed"):
        await reopened.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="late-event",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=result.events[-1],
        )
    await reopened.dispose()


def test_concurrent_identical_evidence_append_is_idempotent() -> None:
    asyncio.run(_concurrent_identical_append())


async def _concurrent_identical_append() -> None:
    database_url = _database_url()
    task = _task("PG-CONCURRENT")
    first_store = PostgresEvidenceStore.from_url(database_url)
    second_store = PostgresEvidenceStore.from_url(database_url)
    project_id = await first_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/concurrent.git",
        default_branch="main",
    )
    run_id = await first_store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="e" * 40,
    )
    event = RunEvent(
        sequence=0,
        state=TaskRunState.PENDING,
        detail="Task run created.",
    )

    ids = await asyncio.gather(
        first_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="state:concurrent",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=event,
            stage="runtime",
            sequence=0,
        ),
        second_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="state:concurrent",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=event,
            stage="runtime",
            sequence=0,
        ),
    )

    assert ids[0] == ids[1]
    evidence = await first_store.list_evidence(
        run_id,
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
    )
    assert len(evidence) == 1
    await first_store.dispose()
    await second_store.dispose()


def test_running_multi_task_run_survives_store_restart_with_run_level_evidence() -> None:
    asyncio.run(_partial_multi_task_recovery())


async def _partial_multi_task_recovery() -> None:
    database_url = _database_url()
    tasks = [_task("PG-A"), _task("PG-B")]
    repository_url = f"https://example.test/{uuid4()}/multi.git"
    store = PostgresEvidenceStore.from_url(database_url)
    project_id = await store.ensure_project(
        repository_url=repository_url,
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=tasks,
        base_commit="b" * 40,
    )
    merge_snapshot = MergeQueueSnapshot(
        integration_ref="refs/heads/integration/test",
        run_base_commit="b" * 40,
        head_commit="b" * 40,
        integrated_task_ids=(),
        attempts=(),
        stopped=False,
    )
    await store.append_evidence(
        run_id=run_id,
        task_id=None,
        evidence_key="merge:snapshot:0000",
        kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
        payload_model=merge_snapshot,
        stage="merge",
        sequence=0,
    )
    await store.dispose()

    reopened = PostgresEvidenceStore.from_url(database_url)
    snapshot = await reopened.load_run(run_id)

    assert snapshot.status.value == "RUNNING"
    assert {item.task.task_id for item in snapshot.tasks} == {"PG-A", "PG-B"}
    assert snapshot.terminal_result is None
    assert snapshot.evidence[0].task_id is None
    assert snapshot.evidence[0].kind is PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT
    await reopened.dispose()


def test_persisted_payload_hash_detects_database_tampering() -> None:
    asyncio.run(_tamper_detection())


async def _tamper_detection() -> None:
    database_url = _database_url()
    task = _task("PG-TAMPER")
    store = PostgresEvidenceStore.from_url(database_url)
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/tamper.git",
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="c" * 40,
    )
    evidence_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="state:0000",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=RunEvent(
            sequence=0,
            state=TaskRunState.PENDING,
            detail="Task run created.",
        ),
    )

    tamper_engine = create_postgres_engine(database_url)
    tamper_sessions = create_session_factory(tamper_engine)
    async with tamper_sessions.begin() as session:
        await session.execute(
            update(EvidenceRow)
            .where(EvidenceRow.id == evidence_id)
            .values(payload={"sequence": 999, "state": "FAILED", "detail": "forged"})
        )
    await tamper_engine.dispose()

    with pytest.raises(PersistenceCorruptionError, match="hash mismatch"):
        await store.load_run(run_id)
    await store.dispose()
