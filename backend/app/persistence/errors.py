class PersistenceError(RuntimeError):
    """Base error for durable runtime-evidence persistence."""


class PersistenceConfigurationError(PersistenceError):
    """Raised when the PostgreSQL persistence boundary is misconfigured."""


class PersistenceConflictError(PersistenceError):
    """Raised when an idempotency key is reused for different evidence."""


class PersistenceCorruptionError(PersistenceError):
    """Raised when persisted payload/hash/type evidence cannot be validated."""
