from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.persistence.errors import StaleRunTokenError
from app.persistence.models import TaskRow


async def database_time(session: AsyncSession) -> datetime:
    """Return PostgreSQL wall-clock time for lease/fence decisions."""

    observed_at = await session.scalar(select(func.clock_timestamp()))
    if not isinstance(observed_at, datetime):
        raise RuntimeError("PostgreSQL did not return a timezone-aware fencing timestamp")
    if observed_at.tzinfo is None:
        raise RuntimeError("PostgreSQL fencing timestamp must include timezone information")
    return observed_at


def assert_current_run_token(task: TaskRow, *, run_token: UUID | None) -> None:
    """Require exact equality with the task's current fencing generation."""

    if task.run_token is None:
        raise StaleRunTokenError("task does not have a fenced execution generation")
    if run_token is None:
        raise StaleRunTokenError("current run_token is required for this task write")
    if task.run_token != run_token:
        raise StaleRunTokenError("run_token is stale for the current task execution generation")


def assert_live_current_run_token(
    task: TaskRow,
    *,
    run_token: UUID | None,
    observed_at: datetime,
) -> None:
    """Require the current token and a lease that is still live at database time."""

    assert_current_run_token(task, run_token=run_token)
    if task.lease_released_at is not None:
        raise StaleRunTokenError("released task generations cannot authorize mutable writes")
    if task.lease_until is None or task.lease_until <= observed_at:
        raise StaleRunTokenError("expired task generations cannot authorize mutable writes")
