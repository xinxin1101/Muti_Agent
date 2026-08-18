from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.lease import TaskLeaseGrant, TaskLeaseSnapshot, TaskLeaseState
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import TaskLeaseConflictError, TaskLeaseExpiredError
from app.persistence.fencing import (
    assert_current_run_token,
    assert_live_current_run_token,
    database_time,
)
from app.persistence.models import RunRow, TaskRow
from app.persistence.types import PersistedRunStatus


class PostgresTaskLeaseStore:
    """PostgreSQL authority for task ownership, liveness, and fencing generations.

    Every acquisition issues a fresh `run_token`. An EXPIRED generation may be replaced by a newer
    generation, but ACTIVE and RELEASED generations are never silently reassigned. The token is a
    capability used at mutable worker write boundaries; it is intentionally absent from ordinary
    lease snapshots and Redis messages.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        echo: bool = False,
    ) -> PostgresTaskLeaseStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def acquire_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        lease_seconds: float,
    ) -> TaskLeaseGrant:
        task_name = self._required_text(task_id, "task_id", max_length=128)
        owner = self._required_text(owner_id, "owner_id", max_length=255)
        duration = self._lease_duration(lease_seconds)

        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            if run.status != PersistedRunStatus.RUNNING.value:
                raise TaskLeaseConflictError("task leases can be acquired only for RUNNING runs")
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)

            if task.lease_owner is None:
                next_generation = 1
            else:
                existing = self._snapshot(task, observed_at=observed_at)
                if existing.state is TaskLeaseState.ACTIVE:
                    raise TaskLeaseConflictError(
                        "task already has an ACTIVE execution generation "
                        f"(generation={existing.generation}, owner={existing.owner_id})"
                    )
                if existing.state is TaskLeaseState.RELEASED:
                    raise TaskLeaseConflictError(
                        "released task generations are terminal ownership history and cannot be reused"
                    )
                if existing.state is not TaskLeaseState.EXPIRED:
                    raise TaskLeaseConflictError("task lease state cannot be safely acquired")
                next_generation = task.lease_generation + 1

            token = uuid4()
            task.lease_owner = owner
            task.lease_dispatch_id = dispatch_id
            task.lease_generation = next_generation
            task.run_token = token
            task.lease_acquired_at = observed_at
            task.heartbeat_at = observed_at
            task.lease_until = observed_at + duration
            task.lease_released_at = None
            await session.flush()
            return TaskLeaseGrant(
                snapshot=self._snapshot(task, observed_at=observed_at),
                run_token=token,
            )

    async def renew_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        run_token: UUID,
        lease_seconds: float,
    ) -> TaskLeaseSnapshot:
        task_name = self._required_text(task_id, "task_id", max_length=128)
        owner = self._required_text(owner_id, "owner_id", max_length=255)
        duration = self._lease_duration(lease_seconds)

        async with self._session_factory.begin() as session:
            # Terminal Run cleanup remains valid only for the exact current generation. New
            # acquisition still requires RUNNING, so this does not authorize new terminal work.
            await self._locked_run(session, run_id)
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)
            self._assert_identity(
                task,
                owner_id=owner,
                dispatch_id=dispatch_id,
                run_token=run_token,
            )
            if task.lease_released_at is not None:
                raise TaskLeaseConflictError("released task leases cannot be renewed")
            assert task.lease_until is not None
            if task.lease_until <= observed_at:
                raise TaskLeaseExpiredError(
                    "expired task generations cannot be resurrected by heartbeat"
                )

            task.heartbeat_at = observed_at
            task.lease_until = observed_at + duration
            await session.flush()
            return self._snapshot(task, observed_at=observed_at)

    async def release_task_lease(
        self,
        *,
        run_id: UUID,
        task_id: str,
        owner_id: str,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> TaskLeaseSnapshot:
        task_name = self._required_text(task_id, "task_id", max_length=128)
        owner = self._required_text(owner_id, "owner_id", max_length=255)

        async with self._session_factory.begin() as session:
            await self._locked_run(session, run_id)
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)
            self._assert_identity(
                task,
                owner_id=owner,
                dispatch_id=dispatch_id,
                run_token=run_token,
            )
            if task.lease_released_at is not None:
                return self._snapshot(task, observed_at=observed_at)
            assert task.lease_until is not None
            if task.lease_until <= observed_at:
                raise TaskLeaseExpiredError(
                    "expired task generations remain EXPIRED and cannot be rewritten as RELEASED"
                )

            task.lease_released_at = observed_at
            await session.flush()
            return self._snapshot(task, observed_at=observed_at)

    @asynccontextmanager
    async def guard_task_publication(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> AsyncIterator[TaskLeaseSnapshot]:
        """Serialize Git ref publication against ownership transfer.

        The task row lock is held across the caller's bounded Git ref publication. Therefore a
        takeover cannot install generation N+1 between the final token check and generation N's Git
        `update-ref`. A publication that enters this guard linearizes before any later takeover.
        """

        task_name = self._required_text(task_id, "task_id", max_length=128)
        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            if run.status != PersistedRunStatus.RUNNING.value:
                raise TaskLeaseConflictError("Git publication requires a RUNNING persisted run")
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)
            if task.lease_dispatch_id != dispatch_id:
                raise TaskLeaseConflictError("Git publication dispatch does not own this task")
            assert_live_current_run_token(
                task,
                run_token=run_token,
                observed_at=observed_at,
            )
            yield self._snapshot(task, observed_at=observed_at)

    async def inspect_task_lease(self, *, run_id: UUID, task_id: str) -> TaskLeaseSnapshot:
        task_name = self._required_text(task_id, "task_id", max_length=128)
        async with self._session_factory() as session:
            task = await session.get(TaskRow, (run_id, task_name))
            if task is None:
                raise ValueError(f"unknown persisted task {task_name!r} for run {run_id}")
            observed_at = await database_time(session)
            return self._snapshot(task, observed_at=observed_at)

    async def list_task_leases(self, run_id: UUID) -> tuple[TaskLeaseSnapshot, ...]:
        async with self._session_factory() as session:
            run_exists = await session.scalar(select(RunRow.id).where(RunRow.id == run_id))
            if run_exists is None:
                raise ValueError(f"unknown persistence run: {run_id}")
            tasks = (
                await session.execute(
                    select(TaskRow).where(TaskRow.run_id == run_id).order_by(TaskRow.task_id)
                )
            ).scalars().all()
            observed_at = await database_time(session)
            return tuple(self._snapshot(task, observed_at=observed_at) for task in tasks)

    async def list_expired_task_leases(
        self,
        *,
        run_id: UUID | None = None,
    ) -> tuple[TaskLeaseSnapshot, ...]:
        async with self._session_factory() as session:
            observed_at = await database_time(session)
            statement = (
                select(TaskRow)
                .join(RunRow, RunRow.id == TaskRow.run_id)
                .where(
                    RunRow.status == PersistedRunStatus.RUNNING.value,
                    TaskRow.lease_owner.is_not(None),
                    TaskRow.lease_released_at.is_(None),
                    TaskRow.lease_until <= observed_at,
                )
            )
            if run_id is not None:
                statement = statement.where(TaskRow.run_id == run_id)
            ordered = statement.order_by(TaskRow.run_id, TaskRow.task_id)
            tasks = (await session.execute(ordered)).scalars().all()
            return tuple(self._snapshot(task, observed_at=observed_at) for task in tasks)

    async def _locked_run(self, session: AsyncSession, run_id: UUID) -> RunRow:
        run = (
            await session.execute(select(RunRow).where(RunRow.id == run_id).with_for_update())
        ).scalar_one_or_none()
        if run is None:
            raise ValueError(f"unknown persistence run: {run_id}")
        return run

    async def _locked_task(self, session: AsyncSession, run_id: UUID, task_id: str) -> TaskRow:
        task = (
            await session.execute(
                select(TaskRow)
                .where(TaskRow.run_id == run_id, TaskRow.task_id == task_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            raise ValueError(f"unknown persisted task {task_id!r} for run {run_id}")
        return task

    @staticmethod
    def _assert_identity(
        task: TaskRow,
        *,
        owner_id: str,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> None:
        if task.lease_owner is None:
            raise TaskLeaseConflictError("task does not have an acquired lease")
        assert_current_run_token(task, run_token=run_token)
        if task.lease_owner != owner_id or task.lease_dispatch_id != dispatch_id:
            raise TaskLeaseConflictError("task lease is owned by a different worker or dispatch")

    @staticmethod
    def _snapshot(task: TaskRow, *, observed_at) -> TaskLeaseSnapshot:
        if task.lease_owner is None:
            state = TaskLeaseState.UNOWNED
        elif task.lease_released_at is not None:
            state = TaskLeaseState.RELEASED
        else:
            assert task.lease_until is not None
            state = (
                TaskLeaseState.EXPIRED
                if task.lease_until <= observed_at
                else TaskLeaseState.ACTIVE
            )
        return TaskLeaseSnapshot(
            run_id=task.run_id,
            task_id=task.task_id,
            state=state,
            generation=task.lease_generation,
            owner_id=task.lease_owner,
            dispatch_id=task.lease_dispatch_id,
            acquired_at=task.lease_acquired_at,
            heartbeat_at=task.heartbeat_at,
            lease_until=task.lease_until,
            released_at=task.lease_released_at,
            observed_at=observed_at,
        )

    @staticmethod
    def _lease_duration(value: float) -> timedelta:
        if value <= 0 or value > 86_400:
            raise ValueError("lease_seconds must be greater than zero and at most 86400")
        return timedelta(seconds=value)

    @staticmethod
    def _required_text(value: str, name: str, *, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{name} exceeds maximum length {max_length}")
        return normalized
