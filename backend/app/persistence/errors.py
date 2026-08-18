class PersistenceError(RuntimeError):
    """Base error for durable runtime-evidence persistence."""


class PersistenceConfigurationError(PersistenceError):
    """Raised when the PostgreSQL persistence boundary is misconfigured."""


class PersistenceConflictError(PersistenceError):
    """Raised when an idempotency key or durable state transition conflicts."""


class PersistenceCorruptionError(PersistenceError):
    """Raised when persisted payload/hash/type evidence cannot be validated."""


class TaskLeaseConflictError(PersistenceConflictError):
    """Raised when task execution ownership conflicts with an existing lease."""


class TaskLeaseExpiredError(TaskLeaseConflictError):
    """Raised when a worker attempts to renew or release an expired task lease."""


class StaleRunTokenError(PersistenceConflictError):
    """Raised when a mutable task write is not authorized by the live fencing generation."""
