from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.lifecycle import RunDisplayStatus
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError
from app.persistence.types import PersistedRunStatus


@dataclass(frozen=True)
class RunRecoveryObservation:
    run_id: UUID
    persisted_status: PersistedRunStatus
    display_status: RunDisplayStatus
    reason: str
    observed_at: datetime
    started_at: datetime
    base_commit: str
    task_count: int
    dispatch_count: int
    completed_worker_count: int
    completed_task_ids: tuple[str, ...]
    checkpointed_task_ids: tuple[str, ...]
    remaining_task_ids: tuple[str, ...]
    last_progress_at: datetime | None
    recovery_run_id: UUID | None


class PostgresRunRecoveryStore:
    """Durable user-facing liveness projection over immutable Run evidence."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
        startup_timeout_seconds: float = 120.0,
        stale_progress_seconds: float = 180.0,
    ) -> None:
        if startup_timeout_seconds <= 0 or stale_progress_seconds <= 0:
            raise ValueError("recovery thresholds must be positive")
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._owns_engine = owns_engine
        self._startup_timeout = timedelta(seconds=startup_timeout_seconds)
        self._stale_progress = timedelta(seconds=stale_progress_seconds)

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        echo: bool = False,
        startup_timeout_seconds: float = 120.0,
        stale_progress_seconds: float = 180.0,
    ) -> PostgresRunRecoveryStore:
        return cls(
            engine=create_postgres_engine(database_url, echo=echo),
            owns_engine=True,
            startup_timeout_seconds=startup_timeout_seconds,
            stale_progress_seconds=stale_progress_seconds,
        )

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def inspect(self, run_id: UUID) -> RunRecoveryObservation:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        """
                        SELECT r.id, r.status, r.started_at, r.base_commit,
                               r.display_status, r.recovery_reason, r.recovery_run_id,
                               (SELECT count(*) FROM tasks t WHERE t.run_id = r.id) AS task_count,
                               (SELECT count(*) FROM dispatch_attempts d WHERE d.run_id = r.id)
                                   AS dispatch_count,
                               (SELECT count(*) FROM evidence_records e
                                  WHERE e.run_id = r.id AND e.kind = 'WORKER_EXECUTION')
                                   AS worker_count,
                               (SELECT max(e.created_at) FROM runtime_events e
                                  WHERE e.run_id = r.id AND e.kind <> 'LEASE_HEARTBEAT')
                                   AS last_progress_at,
                               EXISTS (SELECT 1 FROM tasks t
                                  WHERE t.run_id = r.id AND t.lease_owner IS NOT NULL
                                    AND t.lease_released_at IS NULL AND t.lease_until > now())
                                   AS has_active_lease,
                               EXISTS (SELECT 1 FROM tasks t
                                  WHERE t.run_id = r.id AND t.lease_released_at IS NOT NULL
                                    AND NOT EXISTS (
                                      SELECT 1 FROM evidence_records e
                                       WHERE e.run_id = r.id AND e.task_id = t.task_id
                                         AND e.kind = 'WORKER_EXECUTION'
                                         AND e.payload->>'dispatch_id' = t.lease_dispatch_id::text
                                    )) AS has_released_evidence_gap,
                               COALESCE((SELECT array_agg(e.task_id ORDER BY e.task_id)
                                  FROM evidence_records e
                                 WHERE e.run_id = r.id AND e.kind = 'WORKER_EXECUTION'
                                   AND e.payload->>'status' = 'SUCCEEDED'), ARRAY[]::text[])
                                   AS completed_task_ids,
                               COALESCE((SELECT array_agg(e.task_id ORDER BY e.task_id)
                                  FROM evidence_records e
                                 WHERE e.run_id = r.id AND e.kind = 'WORKER_EXECUTION'
                                   AND e.payload->'checkpoint' IS NOT NULL), ARRAY[]::text[])
                                   AS checkpointed_task_ids
                          FROM runs r
                         WHERE r.id = :run_id
                        """
                    ),
                    {"run_id": run_id},
                )
            ).mappings().one_or_none()
            if row is None:
                raise ValueError(f"unknown persistence run: {run_id}")
            observed_at = (
                await session.execute(text("SELECT now() AS observed_at"))
            ).scalar_one()

        persisted_status = PersistedRunStatus(row["status"])
        completed = tuple(item for item in (row["completed_task_ids"] or ()) if item)
        checkpointed = tuple(
            item for item in (row["checkpointed_task_ids"] or ()) if item and item not in completed
        )
        task_ids = await self._task_ids(run_id)
        remaining = tuple(item for item in task_ids if item not in set(completed))
        display_status, reason = self._classify(
            persisted_status=persisted_status,
            started_at=row["started_at"],
            observed_at=observed_at,
            dispatch_count=int(row["dispatch_count"]),
            last_progress_at=row["last_progress_at"],
            has_active_lease=bool(row["has_active_lease"]),
            has_released_evidence_gap=bool(row["has_released_evidence_gap"]),
        )
        observation = RunRecoveryObservation(
            run_id=run_id,
            persisted_status=persisted_status,
            display_status=display_status,
            reason=reason,
            observed_at=observed_at,
            started_at=row["started_at"],
            base_commit=row["base_commit"],
            task_count=int(row["task_count"]),
            dispatch_count=int(row["dispatch_count"]),
            completed_worker_count=int(row["worker_count"]),
            completed_task_ids=completed,
            checkpointed_task_ids=checkpointed,
            remaining_task_ids=remaining,
            last_progress_at=row["last_progress_at"],
            recovery_run_id=row["recovery_run_id"],
        )
        await self._persist_projection(observation)
        return observation

    async def refresh_running(self, *, project_id: UUID | None = None, limit: int = 100) -> int:
        async with self._session_factory() as session:
            statement = "SELECT id FROM runs WHERE status = 'RUNNING'"
            params: dict[str, Any] = {"limit": limit}
            if project_id is not None:
                statement += " AND project_id = :project_id"
                params["project_id"] = project_id
            statement += " ORDER BY started_at LIMIT :limit"
            run_ids = (await session.execute(text(statement), params)).scalars().all()
        for run_id in run_ids:
            await self.inspect(run_id)
        return len(run_ids)

    async def recovered_run_id(self, source_run_id: UUID) -> UUID | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT recovery_run_id FROM runs WHERE id = :run_id"),
                {"run_id": source_run_id},
            )
            return result.scalar_one_or_none()

    async def link_recovery(self, *, source_run_id: UUID, recovered_run_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE runs SET recovery_run_id = :recovered WHERE id = :source "
                    "AND recovery_run_id IS NULL"
                ),
                {"source": source_run_id, "recovered": recovered_run_id},
            )
            if result.rowcount == 1:
                return
            existing = await session.execute(
                text("SELECT recovery_run_id FROM runs WHERE id = :source"),
                {"source": source_run_id},
            )
            current = existing.scalar_one_or_none()
            if current != recovered_run_id:
                raise PersistenceConflictError("该运行已经创建了另一个恢复运行")

    async def set_resumed_from(self, *, run_id: UUID, source_run_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text("UPDATE runs SET resumed_from_run_id = :source WHERE id = :run_id"),
                {"run_id": run_id, "source": source_run_id},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence run: {run_id}")

    @asynccontextmanager
    async def recovery_lock(self, run_id: UUID):
        async with self._engine.connect() as connection, connection.begin():
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"run-recovery:{run_id}"},
            )
            yield

    async def _task_ids(self, run_id: UUID) -> tuple[str, ...]:
        async with self._session_factory() as session:
            rows = await session.execute(
                text("SELECT task_id FROM tasks WHERE run_id = :run_id ORDER BY task_id"),
                {"run_id": run_id},
            )
        return tuple(rows.scalars().all())

    async def _persist_projection(self, observation: RunRecoveryObservation) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE runs SET display_status = :display_status, recovery_reason = :reason, "
                    "recovery_checked_at = :checked_at WHERE id = :run_id"
                ),
                {
                    "run_id": observation.run_id,
                    "display_status": observation.display_status.value,
                    "reason": (
                        observation.reason
                        if observation.display_status is not RunDisplayStatus.RUNNING
                        else None
                    ),
                    "checked_at": observation.observed_at,
                },
            )

    def _classify(
        self,
        *,
        persisted_status: PersistedRunStatus,
        started_at: datetime,
        observed_at: datetime,
        dispatch_count: int,
        last_progress_at: datetime | None,
        has_active_lease: bool,
        has_released_evidence_gap: bool,
    ) -> tuple[RunDisplayStatus, str]:
        if persisted_status is PersistedRunStatus.SUCCEEDED:
            return RunDisplayStatus.SUCCEEDED, "运行已成功完成"
        if persisted_status is PersistedRunStatus.FAILED:
            return RunDisplayStatus.FAILED, "运行已失败"
        age = observed_at - started_at
        stale = last_progress_at is None or observed_at - last_progress_at >= self._stale_progress
        if has_released_evidence_gap:
            return RunDisplayStatus.RECOVERY_REQUIRED, "租约已释放但没有终态 Worker 证据"
        if age >= self._startup_timeout and dispatch_count == 0:
            return RunDisplayStatus.RECOVERY_REQUIRED, "运行启动后超过阈值仍没有任务分派记录"
        if age >= self._stale_progress and stale and not has_active_lease:
            return RunDisplayStatus.RECOVERY_REQUIRED, "超过阈值没有有效开发、验证或修复进展"
        if has_active_lease and stale:
            return RunDisplayStatus.WAITING_EXTERNAL, "Worker 仍持有租约，但近期没有有效进展"
        return RunDisplayStatus.RUNNING, "任务正在根据持久化证据执行"
