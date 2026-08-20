from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.dispatch import (
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
)
from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.models.reconciliation import (
    TaskReconciliationAction,
    TaskReconciliationDecision,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.dispatch import DispatchAttemptRow, PostgresDispatchAttemptStore
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.fencing import database_time
from app.persistence.models import EvidenceRow, RunRow, TaskRow
from app.persistence.serialization import verify_payload_hash
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


class PreparedDispatchPublication:
    """One locked publication window over an already-durable REQUESTED attempt.

    The owning PostgreSQL transaction keeps Run, Task, and dispatch-attempt rows locked while the
    bounded broker publication occurs. Broker outcome facts are written through this object into
    the same transaction. Unexpected exceptions roll this transaction back and leave the earlier
    REQUESTED commit intact, preserving publication ambiguity instead of inventing an outcome.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        row: DispatchAttemptRow,
    ) -> None:
        self._session = session
        self._row = row
        self._resolved = False

    @property
    def dispatch_id(self) -> UUID:
        return self._row.dispatch_id

    async def mark_enqueued(
        self,
        *,
        broker_message_id: str,
        queue_name: str,
    ) -> PersistedDispatchAttempt:
        if self._resolved or self._row.state != DispatchAttemptState.REQUESTED.value:
            raise PersistenceConflictError("prepared dispatch publication is already resolved")
        message_id = self._required_text(
            broker_message_id,
            "broker_message_id",
            max_length=128,
        )
        queue = self._required_text(queue_name, "queue_name", max_length=128)
        observed_at = await database_time(self._session)
        self._row.state = DispatchAttemptState.ENQUEUED.value
        self._row.broker_message_id = message_id
        self._row.queue_name = queue
        self._row.error_code = None
        self._row.error_message = None
        self._row.resolved_at = observed_at
        self._row.updated_at = observed_at
        await self._session.flush()
        self._resolved = True
        return PostgresDispatchAttemptStore._decode(self._row)

    async def mark_publish_failed(
        self,
        *,
        error_code: str,
        error_message: str,
    ) -> PersistedDispatchAttempt:
        if self._resolved or self._row.state != DispatchAttemptState.REQUESTED.value:
            raise PersistenceConflictError("prepared dispatch publication is already resolved")
        code = self._required_text(error_code, "error_code", max_length=64)
        message = self._required_text(error_message, "error_message", max_length=512)
        observed_at = await database_time(self._session)
        self._row.state = DispatchAttemptState.PUBLISH_FAILED.value
        self._row.broker_message_id = None
        self._row.queue_name = None
        self._row.error_code = code
        self._row.error_message = message
        self._row.resolved_at = observed_at
        self._row.updated_at = observed_at
        await self._session.flush()
        self._resolved = True
        return PostgresDispatchAttemptStore._decode(self._row)

    @staticmethod
    def _required_text(value: str, label: str, *, max_length: int) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise ValueError(f"{label} must contain 1-{max_length} characters")
        return normalized


class PostgresTaskReconciliationStore:
    """Locked PostgreSQL authority for preparing and publishing one task recovery action."""

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
    ) -> PostgresTaskReconciliationStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def prepare_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> TaskReconciliationDecision:
        task_name = self._required_text(task_id, "task_id", max_length=128)

        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)
            self._validate_task_lease_shape(task)

            if run.status != PersistedRunStatus.RUNNING.value:
                return self._decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    action=TaskReconciliationAction.NO_ACTION_RUN_TERMINAL,
                    reason="Persisted Run is terminal; reconciliation cannot reopen it.",
                )

            attempts = await self._locked_attempts(session, run_id=run_id, task_id=task_name)
            self._validate_attempt_history(attempts)
            worker_evidence, dispatch_events = await self._validated_worker_side_evidence(
                session,
                run_id=run_id,
                task_id=task_name,
                attempts=attempts,
            )

            if task.lease_owner is None:
                if worker_evidence or dispatch_events:
                    raise PersistenceCorruptionError(
                        f"UNOWNED task {task_name!r} contains worker-side evidence"
                    )
                if attempts:
                    return self._existing_dispatch_decision(
                        run_id=run_id,
                        task=task,
                        observed_at=observed_at,
                        attempt=attempts[-1],
                        reason_prefix="UNOWNED task already has durable dispatch intent",
                    )
                attempt = await self._insert_attempt(
                    session,
                    run_id=run_id,
                    task_id=task_name,
                    attempt_number=1,
                    observed_at=observed_at,
                )
                return self._decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    action=TaskReconciliationAction.PREPARED_DISPATCH,
                    reason=(
                        "UNOWNED task has no durable dispatch history; locked reconciliation "
                        "prepared the first dispatch attempt."
                    ),
                    dispatch_attempt=attempt,
                    publish_allowed=True,
                    recovery_attempt=False,
                )

            assert task.lease_dispatch_id is not None
            current_attempt = self._attempt_for_dispatch(attempts, task.lease_dispatch_id)
            if current_attempt is None:
                raise PersistenceCorruptionError(
                    "owned V1.1 task lease refers to a dispatch missing from the durable ledger"
                )

            current_worker = worker_evidence.get(task.lease_dispatch_id)
            if current_worker is not None:
                evidence_id, _ = current_worker
                return self._decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    action=TaskReconciliationAction.RESUME_TERMINAL_EVIDENCE,
                    reason=(
                        "Terminal WORKER_EXECUTION evidence exists for the current dispatch; "
                        "reconciliation must resume downstream instead of rerunning the task."
                    ),
                    terminal_worker_evidence_id=evidence_id,
                    dispatch_attempt=current_attempt,
                )

            if task.lease_released_at is not None:
                return self._decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    action=TaskReconciliationAction.BLOCKED_RELEASED_EVIDENCE_GAP,
                    reason=(
                        "Current lease generation is RELEASED without terminal worker evidence; "
                        "reconciliation cannot silently reopen released ownership history."
                    ),
                    dispatch_attempt=current_attempt,
                )

            assert task.lease_until is not None
            if task.lease_until > observed_at:
                return self._decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    action=TaskReconciliationAction.WAIT_ACTIVE_OWNER,
                    reason=(
                        "Current lease generation is ACTIVE under fresh database time; "
                        "reconciliation must not race the live owner."
                    ),
                    dispatch_attempt=current_attempt,
                )

            newer_attempts = tuple(
                attempt
                for attempt in attempts
                if attempt.attempt_number > current_attempt.attempt_number
            )
            if len(newer_attempts) > 1:
                raise PersistenceCorruptionError(
                    "multiple dispatch attempts exist beyond the current lease generation"
                )
            if newer_attempts:
                return self._existing_dispatch_decision(
                    run_id=run_id,
                    task=task,
                    observed_at=observed_at,
                    attempt=newer_attempts[0],
                    reason_prefix="Expired generation already has a newer durable dispatch attempt",
                )

            next_attempt_number = attempts[-1].attempt_number + 1
            attempt = await self._insert_attempt(
                session,
                run_id=run_id,
                task_id=task_name,
                attempt_number=next_attempt_number,
                observed_at=observed_at,
            )
            return self._decision(
                run_id=run_id,
                task=task,
                observed_at=observed_at,
                action=TaskReconciliationAction.PREPARED_DISPATCH,
                reason=(
                    "Current lease generation is EXPIRED with no terminal worker evidence and no "
                    "newer dispatch; locked reconciliation prepared one fresh recovery attempt."
                ),
                dispatch_attempt=attempt,
                publish_allowed=True,
                recovery_attempt=True,
            )

    @asynccontextmanager
    async def guard_prepared_publication(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID,
    ) -> AsyncIterator[PreparedDispatchPublication]:
        """Revalidate a prepared attempt and hold authority locks across broker publication.

        REQUESTED was committed by `prepare_task()` before entering this guard. This second
        transaction closes the prepare/send TOCTOU window while retaining the PostgreSQL-first
        dual-write invariant. If the process dies after broker acceptance, this transaction rolls
        back and the earlier REQUESTED row remains the honest durable fact.
        """

        task_name = self._required_text(task_id, "task_id", max_length=128)
        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            task = await self._locked_task(session, run_id, task_name)
            observed_at = await database_time(session)
            self._validate_task_lease_shape(task)
            if run.status != PersistedRunStatus.RUNNING.value:
                raise PersistenceConflictError(
                    "prepared reconciliation became stale because the Run is no longer RUNNING"
                )

            rows = await self._locked_attempt_rows(session, run_id=run_id, task_id=task_name)
            attempts = tuple(PostgresDispatchAttemptStore._decode(row) for row in rows)
            self._validate_attempt_history(attempts)
            if not rows or rows[-1].dispatch_id != dispatch_id:
                raise PersistenceConflictError(
                    "prepared reconciliation is stale because a different latest dispatch exists"
                )
            target_row = rows[-1]
            target = attempts[-1]
            if target.state is not DispatchAttemptState.REQUESTED:
                raise PersistenceConflictError(
                    "prepared reconciliation dispatch is no longer unresolved REQUESTED intent"
                )

            worker_evidence, dispatch_events = await self._validated_worker_side_evidence(
                session,
                run_id=run_id,
                task_id=task_name,
                attempts=attempts,
            )
            if target.dispatch_id in worker_evidence or target.dispatch_id in dispatch_events:
                raise PersistenceConflictError(
                    "prepared dispatch already has worker-side evidence and cannot be "
                    "published again"
                )

            if task.lease_owner is None:
                if worker_evidence or dispatch_events:
                    raise PersistenceCorruptionError(
                        f"UNOWNED task {task_name!r} contains worker-side evidence"
                    )
                if len(attempts) != 1 or target.attempt_number != 1:
                    raise PersistenceConflictError(
                        "UNOWNED publication guard requires the unique initial dispatch attempt"
                    )
            else:
                assert task.lease_dispatch_id is not None
                current_attempt = self._attempt_for_dispatch(attempts, task.lease_dispatch_id)
                if current_attempt is None:
                    raise PersistenceCorruptionError(
                        "owned task lease refers to a dispatch missing from the durable ledger"
                    )
                if worker_evidence.get(task.lease_dispatch_id) is not None:
                    raise PersistenceConflictError(
                        "terminal worker evidence arrived after recovery prepare; rerun is "
                        "forbidden"
                    )
                if task.lease_released_at is not None:
                    raise PersistenceConflictError(
                        "prepared recovery became stale because lease ownership is RELEASED"
                    )
                assert task.lease_until is not None
                if task.lease_until > observed_at:
                    raise PersistenceConflictError(
                        "prepared recovery became stale because the prior generation is ACTIVE"
                    )
                if target.attempt_number != current_attempt.attempt_number + 1:
                    raise PersistenceConflictError(
                        "prepared recovery attempt is not exactly one generation-attempt ahead"
                    )
                newer = [
                    attempt
                    for attempt in attempts
                    if attempt.attempt_number > current_attempt.attempt_number
                ]
                if len(newer) != 1 or newer[0].dispatch_id != target.dispatch_id:
                    raise PersistenceConflictError(
                        "prepared recovery no longer uniquely represents the next dispatch attempt"
                    )

            yield PreparedDispatchPublication(session=session, row=target_row)

    async def _validated_worker_side_evidence(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        task_id: str,
        attempts: Sequence[PersistedDispatchAttempt],
    ) -> tuple[
        dict[UUID, tuple[int, WorkerExecutionEvidence]],
        dict[UUID, dict[WorkerDispatchPhase, int]],
    ]:
        rows = (
            await session.execute(
                select(EvidenceRow)
                .where(
                    EvidenceRow.run_id == run_id,
                    EvidenceRow.task_id == task_id,
                    EvidenceRow.kind.in_(
                        (
                            PersistenceEvidenceKind.WORKER_EXECUTION.value,
                            PersistenceEvidenceKind.DISPATCH_EVENT.value,
                        )
                    ),
                )
                .order_by(EvidenceRow.id)
            )
        ).scalars().all()
        known_dispatches = {attempt.dispatch_id for attempt in attempts}
        workers: dict[UUID, tuple[int, WorkerExecutionEvidence]] = {}
        dispatches: dict[UUID, dict[WorkerDispatchPhase, int]] = {}

        for row in rows:
            verify_payload_hash(
                row.payload,
                row.payload_sha256,
                label=f"reconciliation worker-side evidence {row.id}",
            )
            try:
                kind = PersistenceEvidenceKind(row.kind)
                if kind is PersistenceEvidenceKind.WORKER_EXECUTION:
                    payload = WorkerExecutionEvidence.model_validate(row.payload)
                    self._assert_worker_identity(
                        payload_run_id=payload.run_id,
                        payload_task_id=payload.task_id,
                        payload_dispatch_id=payload.dispatch_id,
                        run_id=run_id,
                        task_id=task_id,
                        known_dispatches=known_dispatches,
                    )
                    if payload.dispatch_id in workers:
                        raise PersistenceCorruptionError(
                            "multiple terminal WORKER_EXECUTION rows exist for one dispatch"
                        )
                    workers[payload.dispatch_id] = (row.id, payload)
                    continue

                if kind is not PersistenceEvidenceKind.DISPATCH_EVENT:
                    raise PersistenceCorruptionError(
                        "unexpected worker-side evidence kind reached reconciliation"
                    )
                dispatch = WorkerDispatchEvent.model_validate(row.payload)
                self._assert_worker_identity(
                    payload_run_id=dispatch.run_id,
                    payload_task_id=dispatch.task_id,
                    payload_dispatch_id=dispatch.dispatch_id,
                    run_id=run_id,
                    task_id=task_id,
                    known_dispatches=known_dispatches,
                )
                phases = dispatches.setdefault(dispatch.dispatch_id, {})
                if dispatch.phase in phases:
                    raise PersistenceCorruptionError(
                        "duplicate worker dispatch phase evidence exists for one dispatch"
                    )
                phases[dispatch.phase] = row.id
            except (ValidationError, ValueError) as exc:
                raise PersistenceCorruptionError(
                    f"worker-side evidence {row.id} failed reconciliation validation: {exc}"
                ) from exc

        for dispatch_id, phases in dispatches.items():
            if WorkerDispatchPhase.COMPLETED in phases and dispatch_id not in workers:
                raise PersistenceCorruptionError(
                    "COMPLETED dispatch evidence exists without terminal WORKER_EXECUTION evidence"
                )
        return workers, dispatches

    def _existing_dispatch_decision(
        self,
        *,
        run_id: UUID,
        task: TaskRow,
        observed_at: datetime,
        attempt: PersistedDispatchAttempt,
        reason_prefix: str,
    ) -> TaskReconciliationDecision:
        if attempt.state is DispatchAttemptState.PUBLISH_FAILED:
            return self._decision(
                run_id=run_id,
                task=task,
                observed_at=observed_at,
                action=TaskReconciliationAction.BLOCKED_PUBLISH_FAILED,
                reason=(
                    f"{reason_prefix}; PUBLISH_FAILED is an observed broker error, not proof of "
                    "non-delivery, so automatic redispatch remains blocked."
                ),
                dispatch_attempt=attempt,
            )
        return self._decision(
            run_id=run_id,
            task=task,
            observed_at=observed_at,
            action=TaskReconciliationAction.WAIT_EXISTING_DISPATCH,
            reason=(
                f"{reason_prefix}; {attempt.state.value} must settle through the existing "
                "dispatch identity rather than another broker publication."
            ),
            dispatch_attempt=attempt,
        )

    async def _locked_attempt_rows(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[DispatchAttemptRow, ...]:
        rows = (
            await session.execute(
                select(DispatchAttemptRow)
                .where(
                    DispatchAttemptRow.run_id == run_id,
                    DispatchAttemptRow.task_id == task_id,
                )
                .order_by(DispatchAttemptRow.attempt_number)
                .with_for_update()
            )
        ).scalars().all()
        return tuple(rows)

    async def _locked_attempts(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]:
        rows = await self._locked_attempt_rows(session, run_id=run_id, task_id=task_id)
        return tuple(PostgresDispatchAttemptStore._decode(row) for row in rows)

    @staticmethod
    def _validate_attempt_history(attempts: Sequence[PersistedDispatchAttempt]) -> None:
        expected = list(range(1, len(attempts) + 1))
        actual = [attempt.attempt_number for attempt in attempts]
        if actual != expected:
            raise PersistenceCorruptionError(
                f"dispatch attempt numbers must be contiguous from one (actual={actual})"
            )

    @staticmethod
    def _attempt_for_dispatch(
        attempts: Sequence[PersistedDispatchAttempt],
        dispatch_id: UUID,
    ) -> PersistedDispatchAttempt | None:
        for attempt in attempts:
            if attempt.dispatch_id == dispatch_id:
                return attempt
        return None

    @staticmethod
    def _assert_worker_identity(
        *,
        payload_run_id: UUID,
        payload_task_id: str,
        payload_dispatch_id: UUID,
        run_id: UUID,
        task_id: str,
        known_dispatches: set[UUID],
    ) -> None:
        if payload_run_id != run_id or payload_task_id != task_id:
            raise PersistenceCorruptionError(
                "worker-side evidence payload identity disagrees with locked Run/Task"
            )
        if payload_dispatch_id not in known_dispatches:
            raise PersistenceCorruptionError(
                "worker-side evidence refers to a dispatch missing from the durable ledger"
            )

    async def _insert_attempt(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        task_id: str,
        attempt_number: int,
        observed_at: datetime,
    ) -> PersistedDispatchAttempt:
        row = DispatchAttemptRow(
            dispatch_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            attempt_number=attempt_number,
            state=DispatchAttemptState.REQUESTED.value,
            requested_at=observed_at,
            updated_at=observed_at,
        )
        session.add(row)
        await session.flush()
        return PostgresDispatchAttemptStore._decode(row)

    @staticmethod
    def _validate_task_lease_shape(task: TaskRow) -> None:
        if task.lease_owner is None:
            if any(
                value is not None
                for value in (
                    task.lease_dispatch_id,
                    task.run_token,
                    task.lease_acquired_at,
                    task.heartbeat_at,
                    task.lease_until,
                    task.lease_released_at,
                )
            ) or task.lease_generation != 0:
                raise PersistenceCorruptionError("UNOWNED task row has impossible lease fields")
            return
        if (
            task.lease_dispatch_id is None
            or task.run_token is None
            or task.lease_acquired_at is None
            or task.heartbeat_at is None
            or task.lease_until is None
            or task.lease_generation < 1
        ):
            raise PersistenceCorruptionError("owned task row has incomplete lease authority")

    @staticmethod
    def _decision(
        *,
        run_id: UUID,
        task: TaskRow,
        observed_at: datetime,
        action: TaskReconciliationAction,
        reason: str,
        terminal_worker_evidence_id: int | None = None,
        dispatch_attempt: PersistedDispatchAttempt | None = None,
        publish_allowed: bool = False,
        recovery_attempt: bool = False,
    ) -> TaskReconciliationDecision:
        return TaskReconciliationDecision(
            run_id=run_id,
            task_id=task.task_id,
            observed_at=observed_at,
            action=action,
            reason=reason,
            lease_generation=task.lease_generation,
            current_dispatch_id=task.lease_dispatch_id,
            terminal_worker_evidence_id=terminal_worker_evidence_id,
            dispatch_attempt=dispatch_attempt,
            publish_allowed=publish_allowed,
            recovery_attempt=recovery_attempt,
        )

    async def _locked_run(self, session: AsyncSession, run_id: UUID) -> RunRow:
        row = (
            await session.execute(select(RunRow).where(RunRow.id == run_id).with_for_update())
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"unknown persistence run: {run_id}")
        return row

    async def _locked_task(
        self,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
    ) -> TaskRow:
        row = (
            await session.execute(
                select(TaskRow)
                .where(TaskRow.run_id == run_id, TaskRow.task_id == task_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ValueError(f"unknown persisted task {task_id!r} for run {run_id}")
        return row

    @staticmethod
    def _required_text(value: str, label: str, *, max_length: int) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > max_length:
            raise ValueError(f"{label} must contain 1-{max_length} characters")
        return normalized