from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import dramatiq
from dramatiq import Actor, Broker

from app.models.dispatch import TaskDispatchEnvelope, WorkerExecutionEvidence

TaskDispatchHandler = Callable[[TaskDispatchEnvelope], Awaitable[WorkerExecutionEvidence]]

ACTOR_NAME = "devflow.execute_task"


def create_task_actor(
    *,
    broker: Broker,
    handler: TaskDispatchHandler,
    queue_name: str,
) -> Actor:
    """Bind the typed DevFlow worker handler to a specific Dramatiq broker."""

    @dramatiq.actor(
        broker=broker,
        actor_name=ACTOR_NAME,
        queue_name=queue_name,
        max_retries=0,
    )
    def execute_task(payload: dict[str, Any]) -> None:
        envelope = TaskDispatchEnvelope.model_validate(payload)
        asyncio.run(handler(envelope))

    return execute_task
