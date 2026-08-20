from __future__ import annotations

from typing import Protocol
from uuid import UUID

from dramatiq import Actor
from dramatiq.errors import BrokerConnectionError

from app.dispatch.errors import TaskDispatchBrokerError
from app.models.dispatch import TaskDispatchEnvelope, TaskDispatchReceipt
from app.models.reconciliation import (
    TaskReconciliationAction,
    TaskReconciliationDecision,
    TaskReconciliationOutcome,
)
from app.persistence.reconciliation import PreparedDispatchPublication

_BROKER_ERROR_CODE = "BROKER_CONNECTION_ERROR"
_BROKER_ERROR_MESSAGE = "Dramatiq broker could not accept reconciliation dispatch"


class TaskReconciliationStore(Protocol):
    async def prepare_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> TaskReconciliationDecision: ...

    def guard_prepared_publication(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID,
    ): ...

    async def dispose(self) -> None: ...


class IdempotentTaskReconciler:
    """Revalidate durable task recovery state and publish at most one newly prepared dispatch."""

    def __init__(
        self,
        *,
        store: TaskReconciliationStore,
        actor: Actor,
    ) -> None:
        self._store = store
        self._actor = actor

    async def dispose(self) -> None:
        await self._store.dispose()

    async def reconcile(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> TaskReconciliationOutcome:
        decision = await self._store.prepare_task(run_id=run_id, task_id=task_id)
        if decision.action is not TaskReconciliationAction.PREPARED_DISPATCH:
            return TaskReconciliationOutcome(decision=decision)

        attempt = decision.dispatch_attempt
        if attempt is None or not decision.publish_allowed:
            raise RuntimeError("prepared reconciliation is missing durable publication authority")

        receipt: TaskDispatchReceipt | None = None
        broker_error: BrokerConnectionError | None = None
        async with self._store.guard_prepared_publication(
            run_id=run_id,
            task_id=task_id,
            dispatch_id=attempt.dispatch_id,
        ) as publication:
            self._assert_publication_identity(publication, attempt.dispatch_id)
            envelope = TaskDispatchEnvelope(
                dispatch_id=attempt.dispatch_id,
                run_id=run_id,
                task_id=task_id,
            )
            try:
                message = self._actor.send(envelope.model_dump(mode="json"))
            except BrokerConnectionError as exc:
                await publication.mark_publish_failed(
                    error_code=_BROKER_ERROR_CODE,
                    error_message=_BROKER_ERROR_MESSAGE,
                )
                broker_error = exc
            else:
                enqueued = await publication.mark_enqueued(
                    broker_message_id=message.message_id,
                    queue_name=self._actor.queue_name,
                )
                receipt = TaskDispatchReceipt(
                    dispatch_id=enqueued.dispatch_id,
                    run_id=enqueued.run_id,
                    task_id=enqueued.task_id,
                    broker_message_id=enqueued.broker_message_id or "",
                    queue_name=enqueued.queue_name or "",
                )

        if broker_error is not None:
            raise TaskDispatchBrokerError(_BROKER_ERROR_MESSAGE) from broker_error
        if receipt is None:
            raise RuntimeError("prepared reconciliation completed without a broker observation")
        return TaskReconciliationOutcome(decision=decision, receipt=receipt)

    @staticmethod
    def _assert_publication_identity(
        publication: PreparedDispatchPublication,
        dispatch_id: UUID,
    ) -> None:
        if publication.dispatch_id != dispatch_id:
            raise RuntimeError("locked publication dispatch identity changed unexpectedly")
