from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.models import RunEvent, SingleTaskRunResult, TaskContract, TaskLeaseState, TaskRunState
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    StaleRunTokenError,
    TaskLeaseConflictError,
)
from app.persistence.types import PersistedRunStatus


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for run-token hardening tests")
    pytest.skip("run-token hardening test requires DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Exercise fail-closed run-token hardening.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Only a legitimately acquired generation may use a token."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


async def _new_run(
    store: PostgresEvidenceStore,
    task_id: str,
) -> tuple[object, TaskContract]:
    task = _task(task_id)
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/hardening.git",
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="a" * 40,
    )
    return run_id, task


def test_unowned_task_rejects_fabricated_token_without_breaking_legacy_local_path() -> None:
    asyncio.run(_unowned_token_boundary())


async def _unowned_token_boundary() -> None:
    store = PostgresEvidenceStore.from_url(_database_url())
    run_id, task = await _new_run(store, "TOKEN-UNOWNED")
    event = RunEvent(
        sequence=0,
        state=TaskRunState.RUNNING,
        detail="Legacy non-worker persistence remains available before ownership acquisition.",
    )

    legacy_id = await store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="legacy:allowed",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=event,
        stage="test",
        sequence=0,
    )
    assert legacy_id > 0

    forged = uuid4()
    with pytest.raises(StaleRunTokenError, match="does not have"):
        await store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="forged:denied",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=event,
            stage="test",
            sequence=1,
            run_token=forged,
        )

    result = SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Done."),
        ],
    )
    with pytest.raises(StaleRunTokenError, match="does not have"):
        await store.finalize_single_task_run(
            run_id=run_id,
            result=result,
            run_token=forged,
        )

    snapshot = await store.load_run(run_id)
    assert snapshot.status is PersistedRunStatus.RUNNING
    await store.dispose()


def test_expired_takeover_requires_new_dispatch_namespace() -> None:
    asyncio.run(_fresh_dispatch_takeover())


async def _fresh_dispatch_takeover() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, task = await _new_run(evidence_store, "TOKEN-FRESH-DISPATCH")
    old_dispatch = uuid4()

    first = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=old_dispatch,
        lease_seconds=0.05,
    )
    assert first.snapshot.generation == 1
    await asyncio.sleep(0.08)

    with pytest.raises(TaskLeaseConflictError, match="fresh dispatch_id"):
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-b",
            dispatch_id=old_dispatch,
            lease_seconds=1.0,
        )

    still_expired = await lease_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert still_expired.state is TaskLeaseState.EXPIRED
    assert still_expired.generation == 1
    assert still_expired.dispatch_id == old_dispatch

    replacement_dispatch = uuid4()
    replacement = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-b",
        dispatch_id=replacement_dispatch,
        lease_seconds=1.0,
    )
    assert replacement.snapshot.state is TaskLeaseState.ACTIVE
    assert replacement.snapshot.generation == 2
    assert replacement.snapshot.dispatch_id == replacement_dispatch
    assert replacement.run_token != first.run_token

    await lease_store.dispose()
    await evidence_store.dispose()
