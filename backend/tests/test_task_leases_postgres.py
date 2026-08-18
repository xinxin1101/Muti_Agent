from __future__ import annotations

import asyncio
import os
from uuid import UUID, uuid4

import pytest

from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskLeaseGrant,
    TaskLeaseState,
    TaskRunState,
)
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    StaleRunTokenError,
    TaskLeaseConflictError,
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
        objective="Exercise durable fenced task execution ownership.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Lease and fencing behavior remains deterministic."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _terminal_result(task_id: str, *, status: TaskRunState) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=status,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=status, detail="Completed."),
        ],
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
    assert initial.generation == 0
    assert initial.owner_id is None
    assert initial.abandoned is False

    grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    acquired = grant.snapshot
    assert acquired.state is TaskLeaseState.ACTIVE
    assert acquired.generation == 1
    assert acquired.owner_id == "worker-a"
    assert acquired.dispatch_id == dispatch_id
    assert acquired.heartbeat_at == acquired.acquired_at
    assert acquired.lease_until is not None
    assert acquired.heartbeat_at is not None
    assert acquired.lease_until > acquired.heartbeat_at
    assert "run_token" not in grant.model_dump()

    with pytest.raises(TaskLeaseConflictError, match="ACTIVE"):
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
        run_token=grant.run_token,
        lease_seconds=1.0,
    )
    assert renewed.state is TaskLeaseState.ACTIVE
    assert renewed.generation == 1
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
            run_token=grant.run_token,
            lease_seconds=1.0,
        )
    with pytest.raises(TaskLeaseConflictError, match="different worker or dispatch"):
        await lease_store.release_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-a",
            dispatch_id=uuid4(),
            run_token=grant.run_token,
        )

    released = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
    )
    assert released.state is TaskLeaseState.RELEASED
    assert released.generation == 1
    assert released.released_at is not None

    repeated_release = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-a",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
    )
    expected_repeat = released.model_copy(update={"observed_at": repeated_release.observed_at})
    assert repeated_release == expected_repeat

    with pytest.raises(TaskLeaseConflictError, match="released"):
        await lease_store.renew_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-a",
            dispatch_id=dispatch_id,
            run_token=grant.run_token,
            lease_seconds=1.0,
        )
    with pytest.raises(TaskLeaseConflictError, match="released"):
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
    assert [item.generation for item in all_leases] == [1, 0]

    await lease_store.dispose()
    await evidence_store.dispose()


def test_concurrent_initial_acquisition_has_exactly_one_generation_one_owner() -> None:
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
    ) -> TaskLeaseGrant | TaskLeaseConflictError:
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
    winners = [item for item in outcomes if isinstance(item, TaskLeaseGrant)]
    conflicts = [item for item in outcomes if isinstance(item, TaskLeaseConflictError)]

    assert len(winners) == 1
    assert len(conflicts) == 1
    winner = winners[0]
    assert winner.snapshot.state is TaskLeaseState.ACTIVE
    assert winner.snapshot.generation == 1

    observed = await first_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert observed.state is TaskLeaseState.ACTIVE
    assert observed.generation == 1
    assert observed.owner_id == winner.snapshot.owner_id
    assert observed.dispatch_id == winner.snapshot.dispatch_id

    await second_store.dispose()
    await first_store.dispose()
    await evidence_store.dispose()


def test_expired_generation_is_fenced_then_safely_taken_over() -> None:
    asyncio.run(_expired_takeover_fencing())


async def _expired_takeover_fencing() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-TAKEOVER")
    task = tasks[0]
    old_dispatch = uuid4()

    old_grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-old",
        dispatch_id=old_dispatch,
        lease_seconds=0.05,
    )
    assert old_grant.snapshot.generation == 1
    await asyncio.sleep(0.08)

    expired = await lease_store.inspect_task_lease(run_id=run_id, task_id=task.task_id)
    assert expired.state is TaskLeaseState.EXPIRED
    assert expired.generation == 1
    assert expired.abandoned is True

    # Expiry itself removes write authority, even before another worker takes over.
    with pytest.raises(StaleRunTokenError, match="expired"):
        await evidence_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="old:expired-before-takeover",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=RunEvent(
                sequence=0,
                state=TaskRunState.RUNNING,
                detail="Old generation must be fenced after lease expiry.",
            ),
            stage="fencing",
            sequence=0,
            run_token=old_grant.run_token,
        )

    new_dispatch = uuid4()
    new_grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-new",
        dispatch_id=new_dispatch,
        lease_seconds=1.0,
    )
    assert new_grant.snapshot.state is TaskLeaseState.ACTIVE
    assert new_grant.snapshot.generation == 2
    assert new_grant.run_token != old_grant.run_token

    with pytest.raises(StaleRunTokenError, match="stale"):
        await lease_store.renew_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-old",
            dispatch_id=old_dispatch,
            run_token=old_grant.run_token,
            lease_seconds=1.0,
        )

    stale_event = RunEvent(
        sequence=1,
        state=TaskRunState.RUNNING,
        detail="Stale generation must not persist evidence after takeover.",
    )
    with pytest.raises(StaleRunTokenError, match="stale"):
        await evidence_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="old:after-takeover",
            kind=PersistenceEvidenceKind.STATE_TRANSITION,
            payload_model=stale_event,
            stage="fencing",
            sequence=1,
            run_token=old_grant.run_token,
        )

    with pytest.raises(StaleRunTokenError, match="stale"):
        await evidence_store.finalize_single_task_run(
            run_id=run_id,
            result=_terminal_result(task.task_id, status=TaskRunState.SUCCEEDED),
            run_token=old_grant.run_token,
        )

    current_event = RunEvent(
        sequence=2,
        state=TaskRunState.RUNNING,
        detail="Current generation may persist evidence.",
    )
    current_id = await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="new:current-token",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=current_event,
        stage="fencing",
        sequence=2,
        run_token=new_grant.run_token,
    )
    repeated_id = await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task.task_id,
        evidence_key="new:current-token",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        payload_model=current_event,
        stage="fencing",
        sequence=2,
        run_token=new_grant.run_token,
    )
    assert repeated_id == current_id

    renewed = await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-new",
        dispatch_id=new_dispatch,
        run_token=new_grant.run_token,
        lease_seconds=1.0,
    )
    assert renewed.generation == 2

    await lease_store.dispose()
    await evidence_store.dispose()


def test_terminal_run_rejects_new_generation_but_allows_current_generation_cleanup() -> None:
    asyncio.run(_terminal_run_lease_boundary())


async def _terminal_run_lease_boundary() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    run_id, tasks = await _new_run(evidence_store, "LEASE-TERMINAL")
    task = tasks[0]
    dispatch_id = uuid4()

    grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        lease_seconds=1.0,
    )
    result = _terminal_result(task.task_id, status=TaskRunState.SUCCEEDED)
    await evidence_store.finalize_single_task_run(
        run_id=run_id,
        result=result,
        run_token=grant.run_token,
    )

    renewed = await lease_store.renew_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
        lease_seconds=1.0,
    )
    assert renewed.state is TaskLeaseState.ACTIVE
    assert renewed.generation == 1

    released = await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-terminal",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
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
