from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.fencing import database_time
from app.persistence.models import PersistenceBase, RunRow, TaskRow
from app.persistence.types import PersistedRunStatus


class DispatchAttemptRow(PersistenceBase):
    """Durable broker-publication observation for one stable dispatch identity."""

    __tablename__ = "dispatch_attempts"
    __table_args__ = (
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_dispatch_attempts_attempt_number",
        ),
        CheckConstraint(
            "state IN ('REQUESTED', 'ENQUEUED', 'PUBLISH_FAILED')",
            name="ck_dispatch_attempts_state",
        ),
        CheckConstraint(
            "(state = 'REQUESTED' AND broker_message_id IS NULL AND queue_name IS NULL "
            "AND error_code IS NULL AND error_message IS NULL AND resolved_at IS NULL) OR "
            "(state = 'ENQUEUED' AND broker_message_id IS NOT NULL AND queue_name IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL AND resolved_at IS NOT NULL) OR "
            "(state = 'PUBLISH_FAILED' AND broker_message_id IS NULL AND queue_name IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_dispatch_attempts_state_shape",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_dispatch_attempts_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id",
            "task_id",
            "attempt_number",
            name="uq_dispatch_attempts_task_number",
        ),
        Index(
            "ix_dispatch_attempts_run_task",
            "run_id",
            "task_id",
            "attempt_number",
        ),
    )

    dispatch_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    queue_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PostgresDispatchAttemptStore:
    """PostgreSQL authority for dispatch intent and observed broker publication outcomes."""

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
    ) -> PostgresDispatchAttemptStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def load(self, dispatch_id: UUID) -> PersistedDispatchAttempt | None:
        async with self._session_factory() as session:
            row = await session.get(DispatchAttemptRow, dispatch_id)
        return None if row is None else self._decode(row)

    async def list_for_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]:
        task_name = self._required_text(task_id, "task_id", max_length=128)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(DispatchAttemptRow)
                    .where(
                        DispatchAttemptRow.run_id == run_id,
                        DispatchAttemptRow.task_id == task_name,
                    )
                    .order_by(DispatchAttemptRow.attempt_number)
                )
            ).scalars().all()
        return tuple(self._decode(row) for row in rows)

    async def begin_initial_attempt(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, bool]:
        """Persist initial dispatch intent before any broker publication call.

        The ordinary dispatcher may create only attempt number one. Recovery attempts belong to
        Step 5.3 and require a fresh locked reconciliation decision.
        """

        task_name = self._required_text(task_id, "task_id", max_length=128)
        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            if run.status != PersistedRunStatus.RUNNING.value:
                raise PersistenceConflictError(
                    "dispatch attempts may be created only for RUNNING persisted Runs"
                )
            await self._locked_task(session, run_id, task_name)

            existing = await session.get(DispatchAttemptRow, dispatch_id)
            if existing is not None:
                current = self._decode(existing)
                if current.run_id != run_id or current.task_id != task_name:
                    raise PersistenceConflictError(
                        "dispatch_id is already bound to a different persisted Run/Task"
                    )
                if current.attempt_number != 1:
                    raise PersistenceCorruptionError(
                        "ordinary dispatch_id unexpectedly refers to a recovery attempt"
                    )
                return current, False

            previous = (
                await session.execute(
                    select(DispatchAttemptRow.dispatch_id)
                    .where(
                        DispatchAttemptRow.run_id == run_id,
                        DispatchAttemptRow.task_id == task_name,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if previous is not None:
                raise PersistenceConflictError(
                    "task already has a durable dispatch attempt; recovery must decide any retry"
                )

            observed_at = await database_time(session)
            row = DispatchAttemptRow(
                dispatch_id=dispatch_id,
                run_id=run_id,
                task_id=task_name,
                attempt_number=1,
                state=DispatchAttemptState.REQUESTED.value,
                requested_at=observed_at,
                updated_at=observed_at,
            )
            session.add(row)
            await session.flush()
            return self._decode(row), True

    async def mark_enqueued(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
        broker_message_id: str,
        queue_name: str,
    ) -> PersistedDispatchAttempt:
        message_id = self._required_text(
            broker_message_id,
            "broker_message_id",
            max_length=128,
        )
        queue = self._required_text(queue_name, "queue_name", max_length=128)
        task_name = self._required_text(task_id, "task_id", max_length=128)

        async with self._session_factory.begin() as session:
            row = await self._locked_attempt(session, dispatch_id)
            current = self._decode(row)
            self._assert_identity(current, run_id=run_id, task_id=task_name)
            if current.state is DispatchAttemptState.ENQUEUED:
                if (
                    current.broker_message_id != message_id
                    or current.queue_name != queue
                ):
                    raise PersistenceConflictError(
                        "ENQUEUED dispatch acknowledgement cannot be replaced by different facts"
                    )
                return current
            if current.state is not DispatchAttemptState.REQUESTED:
                raise PersistenceConflictError(
                    "PUBLISH_FAILED dispatch attempts cannot be rewritten as ENQUEUED"
                )

            observed_at = await database_time(session)
            row.state = DispatchAttemptState.ENQUEUED.value
            row.broker_message_id = message_id
            row.queue_name = queue
            row.error_code = None
            row.error_message = None
            row.resolved_at = observed_at
            row.updated_at = observed_at
            await session.flush()
            return self._decode(row)

    async def mark_publish_failed(
        self,
        *,
        dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
        error_code: str,
        error_message: str,
    ) -> PersistedDispatchAttempt:
        code = self._required_text(error_code, "error_code", max_length=64)
        message = self._required_text(error_message, "error_message", max_length=512)
        task_name = self._required_text(task_id, "task_id", max_length=128)

        async with self._session_factory.begin() as session:
            row = await self._locked_attempt(session, dispatch_id)
            current = self._decode(row)
            self._assert_identity(current, run_id=run_id, task_id=task_name)
            if current.state is DispatchAttemptState.PUBLISH_FAILED:
                if current.error_code != code or current.error_message != message:
                    raise PersistenceConflictError(
                        "PUBLISH_FAILED dispatch observation cannot be replaced by different facts"
                    )
                return current
            if current.state is not DispatchAttemptState.REQUESTED:
                raise PersistenceConflictError(
                    "ENQUEUED dispatch attempts cannot be rewritten as PUBLISH_FAILED"
                )

            observed_at = await database_time(session)
            row.state = DispatchAttemptState.PUBLISH_FAILED.value
            row.broker_message_id = None
            row.queue_name = None
            row.error_code = code
            row.error_message = message
            row.resolved_at = observed_at
            row.updated_at = observed_at
            await session.flush()
            return self._decode(row)

    async def _locked_run(self, session: AsyncSession, run_id: UUID) -> RunRow:
        run = (
            await session.execute(select(RunRow).where(RunRow.id == run_id).with_for_update())
        ).scalar_one_or_none()
        if run is None:
            raise ValueError(f"unknown persistence run: {run_id}")
        return run

    async def _locked_task(
        self,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
    ) -> TaskRow:
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

    async def _locked_attempt(
        self,
        session: AsyncSession,
        dispatch_id: UUID,
    ) -> DispatchAttemptRow:
        row = (
            await session.execute(
                select(DispatchAttemptRow)
                .where(DispatchAttemptRow.dispatch_id == dispatch_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise PersistenceConflictError(
                "dispatch attempt must be durably REQUESTED before recording broker outcome"
            )
        return row

    @staticmethod
    def _assert_identity(
        current: PersistedDispatchAttempt,
        *,
        run_id: UUID,
        task_id: str,
    ) -> None:
        if current.run_id != run_id or current.task_id != task_id:
            raise PersistenceConflictError(
                "dispatch publication result does not match durable Run/Task identity"
            )

    @staticmethod
    def _decode(row: DispatchAttemptRow) -> PersistedDispatchAttempt:
        try:
            return PersistedDispatchAttempt(
                dispatch_id=row.dispatch_id,
                run_id=row.run_id,
                task_id=row.task_id,
                attempt_number=row.attempt_number,
                state=DispatchAttemptState(row.state),
                broker_message_id=row.broker_message_id,
                queue_name=row.queue_name,
                error_code=row.error_code,
                error_message=row.error_message,
                requested_at=row.requested_at,
                resolved_at=row.resolved_at,
                updated_at=row.updated_at,
            )
        except (ValidationError, ValueError) as exc:
            raise PersistenceCorruptionError(
                f"dispatch attempt {row.dispatch_id} failed durable validation: {exc}"
            ) from exc

    @staticmethod
    def _required_text(value: str, name: str, *, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{name} exceeds maximum length {max_length}")
        return normalized
