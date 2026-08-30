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
    task_time_limit_seconds: float = 7_200.0,
) -> Actor:
    """Bind the typed DevFlow worker handler to a specific Dramatiq broker.

    The Actor timeout is deliberately an outer emergency ceiling. Agent-level budgets end first
    and return structured evidence, so the worker can persist a terminal outcome safely.
    """

    if not 1.0 <= task_time_limit_seconds <= 86_400.0:
        raise ValueError("task_time_limit_seconds must be between 1 and 86400")

    @dramatiq.actor(
        broker=broker,
        actor_name=ACTOR_NAME,
        queue_name=queue_name,
        max_retries=0,
        time_limit=int(task_time_limit_seconds * 1_000),
    )
    def execute_task(payload: dict[str, Any]) -> None:
        envelope = TaskDispatchEnvelope.model_validate(payload)
        asyncio.run(handler(envelope))

    return execute_task
