from app.persistence.dag import (
    PersistedDAGSnapshot,
    PersistedDAGSource,
    PersistenceDAGUnavailableError,
    PostgresDAGStore,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.dispatch import PostgresDispatchAttemptStore
from app.persistence.errors import (
    PersistenceConfigurationError,
    PersistenceConflictError,
    PersistenceCorruptionError,
    PersistenceError,
    StaleRunTokenError,
    TaskLeaseConflictError,
    TaskLeaseExpiredError,
)
from app.persistence.leases import PostgresTaskLeaseStore
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.persistence.publication import PostgresGitHubPublicationStore
from app.persistence.reconciliation import (
    PostgresTaskReconciliationStore,
    PreparedDispatchPublication,
)
from app.persistence.types import (
    ContextFingerprintReference,
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)

# Project lifecycle and branch-scoped identity are part of the accepted persistence schema from
# revision 0008 onward. Keep the public store name stable for existing callers while routing all new
# Project creation through the lifecycle-aware implementation.
PostgresEvidenceStore = ProjectAwarePostgresEvidenceStore

__all__ = [
    "ContextFingerprintReference",
    "PersistenceConfigurationError",
    "PersistenceConflictError",
    "PersistenceCorruptionError",
    "PersistenceDAGUnavailableError",
    "PersistenceError",
    "PersistenceEvidenceKind",
    "PersistedDAGSnapshot",
    "PersistedDAGSource",
    "PersistedEvidence",
    "PersistedRunSnapshot",
    "PersistedRunStatus",
    "PersistedTask",
    "PostgresDAGStore",
    "PostgresDispatchAttemptStore",
    "PostgresEvidenceStore",
    "PostgresGitHubPublicationStore",
    "PostgresTaskLeaseStore",
    "PostgresTaskReconciliationStore",
    "PreparedDispatchPublication",
    "StaleRunTokenError",
    "TaskLeaseConflictError",
    "TaskLeaseExpiredError",
    "create_postgres_engine",
    "create_session_factory",
]
