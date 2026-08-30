from app.workers.actor import ACTOR_NAME, create_task_actor
from app.workers.executor import (
    LocalQueuedTaskExecutionBackend,
    ManagedProjectWorkspaceResolver,
    QueuedTaskWorker,
)
from app.workers.lease import LeasedQueuedTaskWorker

__all__ = [
    "ACTOR_NAME",
    "LeasedQueuedTaskWorker",
    "LocalQueuedTaskExecutionBackend",
    "ManagedProjectWorkspaceResolver",
    "QueuedTaskWorker",
    "build_single_task_runner",
    "create_task_actor",
    "execute_task_from_settings",
    "resolve_worker_id",
]


def __getattr__(name: str):
    """Load composition helpers lazily to keep trace-worker imports acyclic.

    ``app.trace.worker`` imports executor types, while runtime composition imports trace-aware
    wrappers. Importing runtime eagerly here therefore made an order-dependent circular import.
    """

    if name in {"build_single_task_runner", "execute_task_from_settings", "resolve_worker_id"}:
        from app.workers import runtime

        return getattr(runtime, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
