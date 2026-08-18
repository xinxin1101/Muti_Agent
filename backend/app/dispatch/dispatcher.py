from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from dramatiq import Actor
from dramatiq.errors import BrokerConnectionError

from app.dispatch.errors import TaskDispatchBrokerError, TaskDispatchRejectedError
from app.models.dispatch import TaskDispatchEnvelope, TaskDispatchReceipt
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus


class PersistedRunReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class DramatiqTaskDispatcher:
    """Validate persisted identity immediately before enqueueing a minimal task message."""

    def __init__(
        self,
        *,
        store: PersistedRunReader,
        actor: Actor,
    ) -> None:
        self._store = store
        self._actor = actor

    async def dispatch(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID | None = None,
    ) -> TaskDispatchReceipt:
        snapshot = await self._store.load_run(run_id)
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
        try:
            message = self._actor.send(envelope.model_dump(mode="json"))
        except BrokerConnectionError as exc:
            raise TaskDispatchBrokerError("Dramatiq broker could not accept task dispatch") from exc

        return TaskDispatchReceipt(
            dispatch_id=envelope.dispatch_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            broker_message_id=message.message_id,
            queue_name=self._actor.queue_name,
        )
