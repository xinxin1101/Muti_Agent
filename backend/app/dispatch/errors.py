class TaskDispatchError(RuntimeError):
    """Base error for the Redis/Dramatiq dispatch boundary."""


class TaskDispatchRejectedError(TaskDispatchError):
    """Raised when persisted Run/Task evidence does not permit dispatch."""


class TaskDispatchBrokerError(TaskDispatchError):
    """Raised when the broker cannot accept a task message."""


class WorkerExecutionBoundaryError(TaskDispatchError):
    """Raised when a queue message cannot enter the worker execution boundary."""
