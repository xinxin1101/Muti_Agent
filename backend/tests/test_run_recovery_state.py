from datetime import UTC, datetime, timedelta

from app.models.lifecycle import RunDisplayStatus
from app.persistence.run_recovery import PostgresRunRecoveryStore
from app.persistence.types import PersistedRunStatus


def _store() -> PostgresRunRecoveryStore:
    store = object.__new__(PostgresRunRecoveryStore)
    store._startup_timeout = timedelta(seconds=120)
    store._stale_progress = timedelta(seconds=180)
    return store


def test_released_lease_without_worker_evidence_requires_recovery() -> None:
    now = datetime.now(UTC)
    status, reason = _store()._classify(
        persisted_status=PersistedRunStatus.RUNNING,
        started_at=now - timedelta(seconds=10),
        observed_at=now,
        dispatch_count=1,
        last_progress_at=now,
        has_active_lease=False,
        has_released_evidence_gap=True,
    )
    assert status is RunDisplayStatus.RECOVERY_REQUIRED
    assert "终态" in reason


def test_started_run_without_dispatch_is_recovery_required_after_threshold() -> None:
    now = datetime.now(UTC)
    status, _ = _store()._classify(
        persisted_status=PersistedRunStatus.RUNNING,
        started_at=now - timedelta(seconds=121),
        observed_at=now,
        dispatch_count=0,
        last_progress_at=now,
        has_active_lease=False,
        has_released_evidence_gap=False,
    )
    assert status is RunDisplayStatus.RECOVERY_REQUIRED


def test_active_lease_without_progress_waits_for_external_response() -> None:
    now = datetime.now(UTC)
    status, _ = _store()._classify(
        persisted_status=PersistedRunStatus.RUNNING,
        started_at=now - timedelta(seconds=300),
        observed_at=now,
        dispatch_count=1,
        last_progress_at=now - timedelta(seconds=181),
        has_active_lease=True,
        has_released_evidence_gap=False,
    )
    assert status is RunDisplayStatus.WAITING_EXTERNAL


def test_terminal_status_is_not_recovery() -> None:
    now = datetime.now(UTC)
    status, _ = _store()._classify(
        persisted_status=PersistedRunStatus.SUCCEEDED,
        started_at=now,
        observed_at=now,
        dispatch_count=0,
        last_progress_at=None,
        has_active_lease=False,
        has_released_evidence_gap=False,
    )
    assert status is RunDisplayStatus.SUCCEEDED
