from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from dramatiq import Actor
from dramatiq.errors import BrokerConnectionError

from app.dispatch.errors import TaskDispatchBrokerError, TaskDispatchRejectedError
from app.models.dispatch import TaskDispatchEnvelope, TaskDispatchReceipt
from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus

_BROKER_ERROR_CODE = "BROKER_CONNECTION_ERROR"
_BROKER_ERROR_MESSAGE = "Dramatiq broker could not accept task dispatch"


class DurablePersistedRunReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class DispatchAttemptLedger(Protocol):
    async def begin_initial_attempt(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, bool]: ...

    async def mark_enqueued(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
        broker_message_id: str,
        queue_name: str,
    ) -> PersistedDispatchAttempt: ...

    async def mark_publish_failed(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> PersistedDispatchAttempt: ...

    async def dispose(self) -> None: ...


class DurableDramatiqTaskDispatcher:
    """Persist dispatch intent before publishing a stable envelope to Dramatiq.

    PostgreSQL and Redis are deliberately not treated as one transaction. A crash after REQUESTED
    but before a durable publication outcome leaves REQUESTED intact, preserving the dual-write
    ambiguity for the later reconciler instead of guessing delivery or issuing a second dispatch.
    """

    def __init__(
        self,
        *,
        run_store: DurablePersistedRunReader,
        ledger: DispatchAttemptLedger,
        actor: Actor,
    ) -> None:
        self._run_store = run_store
        self._ledger = ledger
        self._actor = actor

    async def dispose(self) -> None:
        await self._ledger.dispose()

    async def dispatch(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID | None = None,
    ) -> TaskDispatchReceipt:
        snapshot = await self._run_store.load_run(run_id)
        if snapshot.status is not PersistedRunStatus.RUNNING:
            raise TaskDispatchRejectedError("only persisted RUNNING runs may dispatch tasks")
        if not any(item.task.task_id == task_id for item in snapshot.tasks):
            raise TaskDispatchRejectedError(
                f"task {task_id!r} does not belong to persisted run {run_id}"
            )

        envelope = TaskDispatchEnvelope(
            dispatch_id=dispatch_id or uuid4(),
            run_id=run_id,
            task_id=task_id,
        )
        attempt, created = await self._ledger.begin_initial_attempt(
            dispatch_id=envelope.dispatch_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
        )

        if not created:
            if attempt.state is DispatchAttemptState.ENQUEUED:
                return self._receipt_from_attempt(attempt)
            raise TaskDispatchRejectedError(
                "durable dispatch attempt is unresolved or failed; recovery must decide any retry"
            )

        try:
            message = self._actor.send(envelope.model_dump(mode="json"))
        except BrokerConnectionError as exc:
            await self._ledger.mark_publish_failed(
                dispatch_id=envelope.dispatch_id,
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                error_code=_BROKER_ERROR_CODE,
                error_message=_BROKER_ERROR_MESSAGE,
            )
            raise TaskDispatchBrokerError(_BROKER_ERROR_MESSAGE) from exc

        enqueued = await self._ledger.mark_enqueued(
            dispatch_id=envelope.dispatch_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            broker_message_id=message.message_id,
            queue_name=self._actor.queue_name,
        )
        return self._receipt_from_attempt(enqueued)

    @staticmethod
    def _receipt_from_attempt(attempt: PersistedDispatchAttempt) -> TaskDispatchReceipt:
        if attempt.state is not DispatchAttemptState.ENQUEUED:
            raise TaskDispatchRejectedError(
                "only durably ENQUEUED dispatch attempts can produce queue receipts"
            )
        assert attempt.broker_message_id is not None
        assert attempt.queue_name is not None
        return TaskDispatchReceipt(
            dispatch_id=attempt.dispatch_id,
            run_id=attempt.run_id,
            task_id=attempt.task_id,
            broker_message_id=attempt.broker_message_id,
            queue_name=attempt.queue_name,
        )
