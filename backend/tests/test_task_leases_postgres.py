from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest

from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskLeaseSnapshot,
    TaskLeaseState,
    TaskRunState,
)
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    TaskLeaseConflictError,
    TaskLeaseExpiredError,
)


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for task lease tests")
    pytest.skip("PostgreSQL task lease test requires DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Exercise durable task execution ownership.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Lease behavior remains deterministic."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


async def _new_run(
    evidence_store: PostgresEvidenceStore,
    *task_ids: str,
) -> tuple[UUID, tuple[TaskContract, ...]]:
    tasks = tuple(_task(task_id) for task_id in task_ids)
    project_id = await evidence_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/lease.git",
        default_branch="main",
    )
    run_id = await evidence_store.start_run(
        project_id=project_id,
        tasks=tasks,
        base_commit="a" * 40,
    )
    return run_id, tasks


def test_task_lease_acquire_heartbeat_release_and_conflicts() -> None:
    asyncio.run(_lease_lifecycle())


async def _lease_lifecycle() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-A", "LEASE-B")
    task = tasks[0]
    dispatch_id = uuid4()

    initial = await lease_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert initial.state is TaskLeaseState.UNOWNED
    assert initial.owner_id is None
    assert initial.abandoned is False

    acquired = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    assert acquired.state is TaskLeaseState.ACTIVE
    assert acquired.owner_id == "worker-a"
    assert acquired.dispatch_id == dispatch_id
    assert acquired.heartbeat_at == acquired.acquired_at
    assert acquired.lease_until is not None
    assert acquired.heartbeat_at is not None
    assert acquired.lease_until > acquired.heartbeat_at

    with pytest.raises(TaskLeaseConflictError, match="already has lease history"):
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-b",
            dispatch_id=uuid4(),
            lease_seconds=1.0,
        )

    await asyncio.sleep(0.02)
    renewed = await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    assert renewed.state is TaskLeaseState.ACTIVE
    assert renewed.heartbeat_at is not None
    assert acquired.heartbeat_at is not None
    assert renewed.heartbeat_at > acquired.heartbeat_at
    assert renewed.lease_until is not None
    assert acquired.lease_until is not None
    assert renewed.lease_until > acquired.lease_until

    with pytest.raises(TaskLeaseConflictError, match="different worker or dispatch"):
        await lease_store.renew_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-b",
            dispatch_id=dispatch_id,
            lease_seconds=1.0,
        )
    with pytest.raises(TaskLeaseConflictError, match="different worker or dispatch"):
        await lease_store.release_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-a",
            dispatch_id=uuid4(),
        )

    released = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
    )
    assert released.state is TaskLeaseState.RELEASED
    assert released.released_at is not None

    repeated_release = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
    )
    expected_repeat = released.model_copy(update={"observed_at": repeated_release.observed_at})
    assert repeated_release == expected_repeat

    with pytest.raises(TaskLeaseConflictError, match="released"):
        await lease_store.renew_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-a",
            dispatch_id=dispatch_id,
            lease_seconds=1.0,
        )
    with pytest.raises(TaskLeaseConflictError, match="already has lease history"):
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-c",
            dispatch_id=uuid4(),
            lease_seconds=1.0,
        )

    all_leases = await lease_store.list_task_leases(run_id)
    assert [item.task_id for item in all_leases] == ["LEASE-A", "LEASE-B"]
    assert [item.state for item in all_leases] == [
        TaskLeaseState.RELEASED,
        TaskLeaseState.UNOWNED,
    ]

    await lease_store.dispose()
    await evidence_store.dispose()


def test_concurrent_task_lease_acquisition_has_exactly_one_owner() -> None:
    asyncio.run(_concurrent_acquisition())


async def _concurrent_acquisition() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    first_store = PostgresTaskLeaseStore.from_url(database_url)
    second_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-RACE")
    task = tasks[0]
    first_dispatch = uuid4()
    second_dispatch = uuid4()

    async def attempt(
        store: PostgresTaskLeaseStore,
        *,
        owner_id: str,
        dispatch_id: UUID,
    ) -> TaskLeaseSnapshot | TaskLeaseConflictError:
        try:
            return await store.acquire_task_lease(
                run_id=run_id,
                task_id=task.task_id,
                owner_id=owner_id,
                dispatch_id=dispatch_id,
                lease_seconds=1.0,
            )
        except TaskLeaseConflictError as exc:
            return exc

    outcomes = await asyncio.gather(
        attempt(first_store, owner_id="worker-race-a", dispatch_id=first_dispatch),
        attempt(second_store, owner_id="worker-race-b", dispatch_id=second_dispatch),
    )
    winners = [item for item in outcomes if isinstance(item, TaskLeaseSnapshot)]
    conflicts = [item for item in outcomes if isinstance(item, TaskLeaseConflictError)]

    assert len(winners) == 1
    assert len(conflicts) == 1
    winner = winners[0]
    assert winner.state is TaskLeaseState.ACTIVE

    observed = await first_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert observed.state is TaskLeaseState.ACTIVE
    assert observed.owner_id == winner.owner_id
    assert observed.dispatch_id == winner.dispatch_id

    await second_store.dispose()
    await first_store.dispose()
    await evidence_store.dispose()


def test_expired_lease_is_abandoned_but_not_reassigned_or_resurrected() -> None:
    asyncio.run(_expired_lease_boundary())


async def _expired_lease_boundary() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-EXPIRED")
    task = tasks[0]
    dispatch_id = uuid4()

    await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-old",
        dispatch_id=dispatch_id,
        lease_seconds=0.05,
    )
    await asyncio.sleep(0.08)

    expired = await lease_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert expired.state is TaskLeaseState.EXPIRED
    assert expired.abandoned is True
    assert expired.owner_id == "worker-old"
    assert expired.dispatch_id == dispatch_id

    expired_rows = await lease_store.list_expired_task_leases(run_id=run_id)
    assert len(expired_rows) == 1
    assert expired_rows[0].task_id == task.task_id
    assert expired_rows[0].state is TaskLeaseState.EXPIRED

    with pytest.raises(TaskLeaseExpiredError, match="cannot be resurrected"):
        await lease_store.renew_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-old",
            dispatch_id=dispatch_id,
            lease_seconds=1.0,
        )
    with pytest.raises(TaskLeaseExpiredError, match="remain EXPIRED"):
        await lease_store.release_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-old",
            dispatch_id=dispatch_id,
        )
    with pytest.raises(TaskLeaseConflictError, match="already has lease history"):
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-new",
            dispatch_id=uuid4(),
            lease_seconds=1.0,
        )

    # Step 3.6 deliberately proves only ownership/liveness. Existing evidence writes are not
    # run_token-fenced yet, so an old caller can still append after its lease expired.
    evidence_id = await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="step-3-7-boundary:stale-write-still-possible",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=RunEvent(
            sequence=0,
            state=TaskRunState.RUNNING,
            detail="Step 3.6 does not yet fence stale writers.",
        ),
        stage="step-3-7-boundary",
        sequence=0,
    )
    assert evidence_id >= 1

    await lease_store.dispose()
    await evidence_store.dispose()


def test_terminal_run_rejects_new_lease_but_allows_owned_lease_cleanup() -> None:
    asyncio.run(_terminal_run_lease_boundary())


async def _terminal_run_lease_boundary() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-TERMINAL")
    task = tasks[0]
    dispatch_id = uuid4()

    acquired = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    result = SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )
    await evidence_store.finalize_single_task_run(run_id=run_id, result=result)

    renewed = await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    assert renewed.state is TaskLeaseState.ACTIVE
    assert renewed.heartbeat_at is not None
    assert acquired.heartbeat_at is not None
    assert renewed.heartbeat_at >= acquired.heartbeat_at

    released = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
    )
    assert released.state is TaskLeaseState.RELEASED

    with pytest.raises(TaskLeaseConflictError, match="RUNNING"):
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-late",
            dispatch_id=uuid4(),
            lease_seconds=1.0,
        )

    await lease_store.dispose()
    await evidence_store.dispose()
