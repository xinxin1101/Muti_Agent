from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence
from app.models.lease import TaskLeaseGrant, TaskLeaseSnapshot
from app.persistence.errors import TaskLeaseConflictError


class QueuedWorker(Protocol):
    async def execute(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
    ) -> WorkerExecutionEvidence: ...


class TaskLeaseStore(Protocol):
    async def acquire_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        lease_seconds: float,
    ) -> TaskLeaseGrant: ...

    async def renew_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        run_token: UUID,
        lease_seconds: float,
    ) -> TaskLeaseSnapshot: ...

    async def release_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> TaskLeaseSnapshot: ...


class LeasedQueuedTaskWorker:
    """Wrap one queued worker with lease renewal and generation fencing.

    The fresh token is returned by PostgreSQL acquisition and passed only inside the worker process.
    It never comes from Redis. Heartbeat failure still triggers cooperative cancellation, while the
    persistence and Git publication boundaries independently reject expired/stale generations.
    """

    def __init__(
        self,
        *,
        worker: QueuedWorker,
        lease_store: TaskLeaseStore,
        worker_id: str,
        lease_seconds: float,
        heartbeat_interval_seconds: float,
    ) -> None:
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id or len(normalized_worker_id) > 255:
            raise ValueError("worker_id must contain 1-255 non-whitespace characters")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        if heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be greater than zero")
        if heartbeat_interval_seconds >= lease_seconds:
            raise ValueError("heartbeat interval must be shorter than the lease duration")

        self._worker = worker
        self._lease_store = lease_store
        self._worker_id = normalized_worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def execute(self, envelope: TaskDispatchEnvelope) -> WorkerExecutionEvidence:
        try:
            grant = await self._lease_store.acquire_task_lease(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                owner_id=self._worker_id,
                dispatch_id=envelope.dispatch_id,
                lease_seconds=self._lease_seconds,
            )
        except TaskLeaseConflictError as exc:
            raise WorkerExecutionBoundaryError(
                "queued execution could not acquire a live fenced task generation"
            ) from exc

        run_token = grant.run_token
        stop_heartbeat = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(envelope, run_token, stop_heartbeat),
            name=f"devflow-heartbeat:{envelope.run_id}:{envelope.task_id}",
        )
        execution_task = asyncio.create_task(
            self._execute_generation(envelope, grant),
            name=f"devflow-execution:{envelope.run_id}:{envelope.task_id}",
        )
        heartbeat_failed = False

        try:
            done, _ = await asyncio.wait(
                {execution_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_failed = True
                heartbeat_error = heartbeat_task.exception()
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
                if heartbeat_error is None:
                    raise WorkerExecutionBoundaryError(
                        "task heartbeat stopped before queued execution completed"
                    )
                raise WorkerExecutionBoundaryError(
                    "task heartbeat failed; queued execution was cooperatively cancelled"
                ) from heartbeat_error

            result = await execution_task
            stop_heartbeat.set()
            await heartbeat_task
            await self._release(envelope, run_token)
            return result
        except BaseException:
            stop_heartbeat.set()
            if not execution_task.done():
                execution_task.cancel()
                await asyncio.gather(execution_task, return_exceptions=True)
            if not heartbeat_task.done():
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            if not heartbeat_failed:
                await self._release_after_failure(envelope, run_token)
            raise

    async def _execute_generation(
        self,
        envelope: TaskDispatchEnvelope,
        grant: TaskLeaseGrant,
    ) -> WorkerExecutionEvidence:
        """Use the optional trace-aware extension without breaking the V1 worker protocol."""

        execute_generation = getattr(self._worker, "execute_generation", None)
        if execute_generation is None:
            return await self._worker.execute(
                envelope,
                run_token=grant.run_token,
            )
        return await execute_generation(
            envelope,
            run_token=grant.run_token,
            generation=grant.snapshot.generation,
        )

    async def _heartbeat_loop(
        self,
        envelope: TaskDispatchEnvelope,
        run_token: UUID,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
                return
            except TimeoutError:
                await self._lease_store.renew_task_lease(
                    run_id=envelope.run_id,
                    task_id=envelope.task_id,
                    owner_id=self._worker_id,
                    dispatch_id=envelope.dispatch_id,
                    run_token=run_token,
                    lease_seconds=self._lease_seconds,
                )

    async def _release(self, envelope: TaskDispatchEnvelope, run_token: UUID) -> None:
        try:
            await self._lease_store.release_task_lease(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                owner_id=self._worker_id,
                dispatch_id=envelope.dispatch_id,
                run_token=run_token,
            )
        except TaskLeaseConflictError as exc:
            raise WorkerExecutionBoundaryError(
                "queued execution completed but its fenced task generation was no longer releasable"
            ) from exc

    async def _release_after_failure(
        self,
        envelope: TaskDispatchEnvelope,
        run_token: UUID,
    ) -> None:
        try:
            await self._release(envelope, run_token)
        except WorkerExecutionBoundaryError:
            # Preserve the original execution error. The durable generation remains inspectable as
            # ACTIVE until expiry or EXPIRED after its deadline.
            return