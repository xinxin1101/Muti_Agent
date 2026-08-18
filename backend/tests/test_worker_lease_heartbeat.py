from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskDispatchEnvelope,
    TaskLeaseSnapshot,
    TaskLeaseState,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import TaskLeaseConflictError, TaskLeaseExpiredError
from app.workers.lease import LeasedQueuedTaskWorker


def _envelope(task_id: str = "LEASE-WORKER") -> TaskDispatchEnvelope:
    return TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=uuid4(),
        task_id=task_id,
    )


def _active_snapshot(envelope: TaskDispatchEnvelope, owner_id: str) -> TaskLeaseSnapshot:
    now = datetime.now(timezone.utc)
    return TaskLeaseSnapshot(
        run_id=envelope.run_id,
        task_id=envelope.task_id,
        state=TaskLeaseState.ACTIVE,
        owner_id=owner_id,
        dispatch_id=envelope.dispatch_id,
        acquired_at=now,
        heartbeat_at=now,
        lease_until=now + timedelta(seconds=1),
        observed_at=now,
    )


def _released_snapshot(envelope: TaskDispatchEnvelope, owner_id: str) -> TaskLeaseSnapshot:
    now = datetime.now(timezone.utc)
    return TaskLeaseSnapshot(
        run_id=envelope.run_id,
        task_id=envelope.task_id,
        state=TaskLeaseState.RELEASED,
        owner_id=owner_id,
        dispatch_id=envelope.dispatch_id,
        acquired_at=now - timedelta(milliseconds=20),
        heartbeat_at=now - timedelta(milliseconds=10),
        lease_until=now + timedelta(seconds=1),
        released_at=now,
        observed_at=now,
    )


def _success_evidence(envelope: TaskDispatchEnvelope) -> WorkerExecutionEvidence:
    result = SingleTaskRunResult(
        task_id=envelope.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )
    return WorkerExecutionEvidence(
        dispatch_id=envelope.dispatch_id,
        run_id=envelope.run_id,
        task_id=envelope.task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit="a" * 40,
        branch_name="devflow/task/test",
        commit_sha="b" * 40,
        run_result=result,
        duration_ms=1,
    )


class _FakeLeaseStore:
    def __init__(self, *, fail_acquire: bool = False, fail_renew_at: int | None = None) -> None:
        self.fail_acquire = fail_acquire
        self.fail_renew_at = fail_renew_at
        self.acquire_calls = 0
        self.renew_calls = 0
        self.release_calls = 0

    async def acquire_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        lease_seconds: float,
    ) -> TaskLeaseSnapshot:
        self.acquire_calls += 1
        if self.fail_acquire:
            raise TaskLeaseConflictError("owned")
        return _active_snapshot(
            TaskDispatchEnvelope(dispatch_id=dispatch_id, run_id=run_id, task_id=task_id),
            owner_id,
        )

    async def renew_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        lease_seconds: float,
    ) -> TaskLeaseSnapshot:
        self.renew_calls += 1
        if self.fail_renew_at is not None and self.renew_calls >= self.fail_renew_at:
            raise TaskLeaseExpiredError("heartbeat lost")
        return _active_snapshot(
            TaskDispatchEnvelope(dispatch_id=dispatch_id, run_id=run_id, task_id=task_id),
            owner_id,
        )

    async def release_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
    ) -> TaskLeaseSnapshot:
        self.release_calls += 1
        return _released_snapshot(
            TaskDispatchEnvelope(dispatch_id=dispatch_id, run_id=run_id, task_id=task_id),
            owner_id,
        )


class _FakeQueuedWorker:
    def __init__(self, *, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.calls = 0
        self.cancelled = False

    async def execute(self, envelope: TaskDispatchEnvelope) -> WorkerExecutionEvidence:
        self.calls += 1
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("inner execution failed")
            return _success_evidence(envelope)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_leased_worker_acquires_and_releases_fast_execution() -> None:
    envelope = _envelope()
    lease_store = _FakeLeaseStore()
    inner = _FakeQueuedWorker()
    worker = LeasedQueuedTaskWorker(
        worker=inner,
        lease_store=lease_store,
        worker_id="worker-a",
        lease_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    result = asyncio.run(worker.execute(envelope))

    assert result.status is WorkerExecutionStatus.SUCCEEDED
    assert lease_store.acquire_calls == 1
    assert lease_store.renew_calls == 0
    assert lease_store.release_calls == 1
    assert inner.calls == 1


def test_leased_worker_renews_while_execution_is_active() -> None:
    envelope = _envelope("LEASE-SLOW")
    lease_store = _FakeLeaseStore()
    inner = _FakeQueuedWorker(delay=0.075)
    worker = LeasedQueuedTaskWorker(
        worker=inner,
        lease_store=lease_store,
        worker_id="worker-slow",
        lease_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    result = asyncio.run(worker.execute(envelope))

    assert result.status is WorkerExecutionStatus.SUCCEEDED
    assert lease_store.acquire_calls == 1
    assert lease_store.renew_calls >= 2
    assert lease_store.release_calls == 1


def test_lease_acquisition_conflict_prevents_inner_execution() -> None:
    envelope = _envelope("LEASE-CONFLICT")
    lease_store = _FakeLeaseStore(fail_acquire=True)
    inner = _FakeQueuedWorker()
    worker = LeasedQueuedTaskWorker(
        worker=inner,
        lease_store=lease_store,
        worker_id="worker-conflict",
        lease_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    with pytest.raises(WorkerExecutionBoundaryError, match="exclusive"):
        asyncio.run(worker.execute(envelope))

    assert inner.calls == 0
    assert lease_store.release_calls == 0


def test_heartbeat_failure_cooperatively_cancels_inner_without_releasing() -> None:
    envelope = _envelope("LEASE-HEARTBEAT-FAIL")
    lease_store = _FakeLeaseStore(fail_renew_at=1)
    inner = _FakeQueuedWorker(delay=1.0)
    worker = LeasedQueuedTaskWorker(
        worker=inner,
        lease_store=lease_store,
        worker_id="worker-heartbeat",
        lease_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    with pytest.raises(WorkerExecutionBoundaryError, match="heartbeat failed"):
        asyncio.run(worker.execute(envelope))

    assert inner.calls == 1
    assert inner.cancelled is True
    assert lease_store.renew_calls == 1
    assert lease_store.release_calls == 0


def test_inner_failure_releases_live_lease_before_propagating() -> None:
    envelope = _envelope("LEASE-INNER-FAIL")
    lease_store = _FakeLeaseStore()
    inner = _FakeQueuedWorker(fail=True)
    worker = LeasedQueuedTaskWorker(
        worker=inner,
        lease_store=lease_store,
        worker_id="worker-inner-fail",
        lease_seconds=0.2,
        heartbeat_interval_seconds=0.02,
    )

    with pytest.raises(RuntimeError, match="inner execution failed"):
        asyncio.run(worker.execute(envelope))

    assert lease_store.acquire_calls == 1
    assert lease_store.release_calls == 1


def test_heartbeat_interval_must_be_shorter_than_lease() -> None:
    with pytest.raises(ValueError, match="shorter than the lease"):
        LeasedQueuedTaskWorker(
            worker=_FakeQueuedWorker(),
            lease_store=_FakeLeaseStore(),
            worker_id="worker-invalid",
            lease_seconds=1.0,
            heartbeat_interval_seconds=1.0,
        )
