from __future__ import annotations

import re
from collections.abc import Sequence
from uuid import UUID, uuid4

from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql import func

from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.models.run import SingleTaskRunResult
from app.models.task import TaskContract
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.events import append_runtime_event, decode_runtime_event
from app.persistence.fencing import assert_live_current_run_token, database_time
from app.persistence.models import EvidenceRow, ProjectRow, RunRow, RuntimeEventRow, TaskRow
from app.persistence.serialization import (
    canonical_payload,
    decode_evidence,
    decode_terminal_result,
    verify_payload_hash,
)
from app.persistence.types import (
    ContextFingerprintReference,
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_SCHEMA_VERSION = 1

_EVIDENCE_EVENT_SOURCES = {
    PersistenceEvidenceKind.STATE_TRANSITION: RuntimeEventSource.RUNTIME,
    PersistenceEvidenceKind.DEVELOPER_RUN: RuntimeEventSource.AGENT,
    PersistenceEvidenceKind.VERIFICATION_RESULT: RuntimeEventSource.VERIFICATION,
    PersistenceEvidenceKind.REVIEW_DECISION: RuntimeEventSource.REVIEW,
    PersistenceEvidenceKind.REPAIR_RUN: RuntimeEventSource.REPAIR,
    PersistenceEvidenceKind.FAILURE_REPORT: RuntimeEventSource.RUNTIME,
    PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT: RuntimeEventSource.INTEGRATION,
    PersistenceEvidenceKind.MERGE_CONFLICT: RuntimeEventSource.INTEGRATION,
    PersistenceEvidenceKind.INTEGRATION_GATE: RuntimeEventSource.INTEGRATION,
    PersistenceEvidenceKind.HUMAN_DECISION: RuntimeEventSource.INTEGRATION,
    PersistenceEvidenceKind.CONTEXT_REFERENCE: RuntimeEventSource.RUNTIME,
    PersistenceEvidenceKind.DISPATCH_EVENT: RuntimeEventSource.DISPATCH,
    PersistenceEvidenceKind.WORKER_EXECUTION: RuntimeEventSource.WORKER,
}


class PostgresEvidenceStore:
    """Transactional durability boundary for runtime evidence and event projections.

    Git and validated runtime models remain authoritative. Structured runtime events are a compact,
    queryable observability projection over accepted persistence facts; they never replace typed
    evidence or authorize task success. Task-scoped worker writes become fenced automatically after
    that task has acquired a `run_token`; non-leased/local persistence paths keep Step 3.4 behavior.
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
    ) -> PostgresEvidenceStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def ensure_project(
        self,
        *,
        repository_url: str,
        default_branch: str,
        project_id: UUID | None = None,
    ) -> UUID:
        repository = self._required_text(repository_url, "repository_url", max_length=2000)
        branch = self._required_text(default_branch, "default_branch", max_length=255)
        candidate_id = project_id or uuid4()

        async with self._session_factory.begin() as session:
            statement = (
                insert(ProjectRow)
                .values(
                    id=candidate_id,
                    repository_url=repository,
                    default_branch=branch,
                )
                .on_conflict_do_nothing(index_elements=[ProjectRow.repository_url])
                .returning(ProjectRow.id)
            )
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return inserted

            existing = (
                await session.execute(
                    select(ProjectRow).where(ProjectRow.repository_url == repository)
                )
            ).scalar_one()
            if existing.default_branch != branch:
                raise PersistenceConflictError(
                    "repository URL already exists with a different default branch"
                )
            return existing.id

    async def start_run(
        self,
        *,
        project_id: UUID,
        tasks: Sequence[TaskContract],
        base_commit: str,
        run_id: UUID | None = None,
    ) -> UUID:
        commit = base_commit.strip().lower()
        if _COMMIT_RE.fullmatch(commit) is None:
            raise ValueError("base_commit must be a 40-64 character lowercase Git object id")
        if not tasks:
            raise ValueError("persisted runs require at least one TaskContract")
        task_ids = [task.task_id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("persisted run task ids must be unique")

        candidate_run_id = run_id or uuid4()
        try:
            async with self._session_factory.begin() as session:
                project_exists = await session.scalar(
                    select(ProjectRow.id).where(ProjectRow.id == project_id)
                )
                if project_exists is None:
                    raise ValueError(f"unknown persistence project: {project_id}")

                run = RunRow(
                    id=candidate_run_id,
                    project_id=project_id,
                    base_commit=commit,
                    status=PersistedRunStatus.RUNNING.value,
                    event_sequence=0,
                )
                session.add(run)
                for task in tasks:
                    payload, digest = canonical_payload(task)
                    session.add(
                        TaskRow(
                            run_id=candidate_run_id,
                            task_id=task.task_id,
                            contract=payload,
                            contract_sha256=digest,
                        )
                    )
                await session.flush()
                await append_runtime_event(
                    session,
                    run=run,
                    draft=RuntimeEventDraft(
                        event_key="run:started",
                        kind=RuntimeEventKind.RUN_STARTED,
                        source=RuntimeEventSource.PERSISTENCE,
                        message="Persisted run started.",
                        attributes={
                            "project_id": str(project_id),
                            "base_commit": commit,
                            "task_count": len(tasks),
                        },
                    ),
                )
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "run identity already exists or violates persistence constraints: "
                f"{candidate_run_id}"
            ) from exc
        return candidate_run_id

    async def append_evidence(
        self,
        *,
        run_id: UUID,
        evidence_key: str,
        kind: PersistenceEvidenceKind,
        payload_model: BaseModel,
        task_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
        run_token: UUID | None = None,
    ) -> int:
        key = self._required_text(evidence_key, "evidence_key", max_length=255)
        normalized_task = self._optional_text(task_id, "task_id", max_length=128)
        normalized_stage = self._optional_text(stage, "stage", max_length=64)
        if sequence is not None and sequence < 0:
            raise ValueError("evidence sequence must be non-negative")

        payload, digest = canonical_payload(payload_model)
        decode_evidence(kind, payload)

        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            if run.status != PersistedRunStatus.RUNNING.value:
                raise PersistenceConflictError("terminal persisted runs are append-closed")

            task: TaskRow | None = None
            if normalized_task is not None:
                task = await self._locked_task(session, run_id, normalized_task)
                if run_token is not None or task.run_token is not None:
                    observed_at = await database_time(session)
                    assert_live_current_run_token(
                        task,
                        run_token=run_token,
                        observed_at=observed_at,
                    )

            existing = (
                await session.execute(
                    select(EvidenceRow).where(
                        EvidenceRow.run_id == run_id,
                        EvidenceRow.evidence_key == key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                if self._same_evidence(
                    existing,
                    task_id=normalized_task,
                    kind=kind,
                    stage=normalized_stage,
                    sequence=sequence,
                    payload_sha256=digest,
                ):
                    return existing.id
                raise PersistenceConflictError(
                    f"evidence key {key!r} was reused for different evidence"
                )

            row = EvidenceRow(
                run_id=run_id,
                task_id=normalized_task,
                evidence_key=key,
                kind=kind.value,
                stage=normalized_stage,
                sequence=sequence,
                schema_version=_SCHEMA_VERSION,
                payload=payload,
                payload_sha256=digest,
            )
            session.add(row)
            await session.flush()
            await append_runtime_event(
                session,
                run=run,
                draft=self._evidence_event_draft(
                    row=row,
                    task=task,
                    kind=kind,
                ),
            )
            return row.id

    async def record_context_reference(
        self,
        *,
        run_id: UUID,
        reference: ContextFingerprintReference,
        evidence_key: str,
        sequence: int | None = None,
        run_token: UUID | None = None,
    ) -> int:
        return await self.append_evidence(
            run_id=run_id,
            task_id=reference.task_id,
            evidence_key=evidence_key,
            kind=PersistenceEvidenceKind.CONTEXT_REFERENCE,
            payload_model=reference,
            stage=reference.stage,
            sequence=sequence,
            run_token=run_token,
        )

    async def record_single_task_result_evidence(
        self,
        *,
        run_id: UUID,
        result: SingleTaskRunResult,
        run_token: UUID | None = None,
    ) -> None:
        for event in result.events:
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key=f"state:{event.sequence:04d}",
                kind=PersistenceEvidenceKind.STATE_TRANSITION,
                payload_model=event,
                stage="runtime",
                sequence=event.sequence,
                run_token=run_token,
            )

        if result.developer is not None:
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key="developer:0000",
                kind=PersistenceEvidenceKind.DEVELOPER_RUN,
                payload_model=result.developer,
                stage="developer",
                sequence=0,
                run_token=run_token,
            )

        for index, verification in enumerate(result.verifications):
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key=f"verification:{index:04d}",
                kind=PersistenceEvidenceKind.VERIFICATION_RESULT,
                payload_model=verification,
                stage="verification",
                sequence=index,
                run_token=run_token,
            )

        for index, review in enumerate(result.reviews):
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key=f"review:{index:04d}",
                kind=PersistenceEvidenceKind.REVIEW_DECISION,
                payload_model=review,
                stage="review",
                sequence=index,
                run_token=run_token,
            )

        for repair in result.repairs:
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key=f"repair:{repair.attempt:04d}",
                kind=PersistenceEvidenceKind.REPAIR_RUN,
                payload_model=repair,
                stage="repair",
                sequence=repair.attempt,
                run_token=run_token,
            )

        for index, failure in enumerate(result.failures):
            await self.append_evidence(
                run_id=run_id,
                task_id=result.task_id,
                evidence_key=f"failure:{index:04d}",
                kind=PersistenceEvidenceKind.FAILURE_REPORT,
                payload_model=failure,
                stage="terminal",
                sequence=index,
                run_token=run_token,
            )

        await self.finalize_single_task_run(
            run_id=run_id,
            result=result,
            run_token=run_token,
        )

    async def finalize_single_task_run(
        self,
        *,
        run_id: UUID,
        result: SingleTaskRunResult,
        run_token: UUID | None = None,
    ) -> None:
        payload, digest = canonical_payload(result)
        persisted_status = PersistedRunStatus.from_task_state(result.status)

        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, run_id)
            tasks = (
                await session.execute(
                    select(TaskRow)
                    .where(TaskRow.run_id == run_id)
                    .order_by(TaskRow.task_id)
                    .with_for_update()
                )
            ).scalars().all()
            if len(tasks) != 1 or tasks[0].task_id != result.task_id:
                raise PersistenceConflictError(
                    "SingleTaskRunResult can finalize only a run containing exactly that task"
                )

            task = tasks[0]
            if run_token is not None or task.run_token is not None:
                observed_at = await database_time(session)
                assert_live_current_run_token(
                    task,
                    run_token=run_token,
                    observed_at=observed_at,
                )

            if run.status != PersistedRunStatus.RUNNING.value:
                if (
                    run.status == persisted_status.value
                    and run.terminal_result_sha256 == digest
                    and run.terminal_result == payload
                ):
                    return
                raise PersistenceConflictError(
                    "persisted run was already finalized with different terminal evidence"
                )

            run.status = persisted_status.value
            run.terminal_result = payload
            run.terminal_result_sha256 = digest
            run.finished_at = func.now()
            await session.flush()
            await append_runtime_event(
                session,
                run=run,
                draft=RuntimeEventDraft(
                    event_key="run:finalized",
                    kind=RuntimeEventKind.RUN_FINALIZED,
                    source=RuntimeEventSource.PERSISTENCE,
                    level=(
                        RuntimeEventLevel.ERROR
                        if persisted_status is PersistedRunStatus.FAILED
                        else RuntimeEventLevel.INFO
                    ),
                    task_id=result.task_id,
                    dispatch_id=task.lease_dispatch_id,
                    generation=task.lease_generation or None,
                    message=f"Persisted run finalized as {persisted_status.value}.",
                    attributes={
                        "status": persisted_status.value,
                        "terminal_result_sha256": digest,
                    },
                ),
            )

    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot:
        async with self._session_factory() as session:
            joined = (
                await session.execute(
                    select(RunRow, ProjectRow)
                    .join(ProjectRow, ProjectRow.id == RunRow.project_id)
                    .where(RunRow.id == run_id)
                )
            ).one_or_none()
            if joined is None:
                raise ValueError(f"unknown persistence run: {run_id}")
            run, project = joined

            task_rows = (
                await session.execute(
                    select(TaskRow).where(TaskRow.run_id == run_id).order_by(TaskRow.task_id)
                )
            ).scalars().all()
            evidence_rows = (
                await session.execute(
                    select(EvidenceRow)
                    .where(EvidenceRow.run_id == run_id)
                    .order_by(EvidenceRow.id)
                )
            ).scalars().all()

        tasks = tuple(self._decode_task(row) for row in task_rows)
        evidence = tuple(self._decode_evidence_row(row) for row in evidence_rows)
        try:
            status = PersistedRunStatus(run.status)
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted run has unknown status {run.status!r}"
            ) from exc

        terminal_result = run.terminal_result
        terminal_sha = run.terminal_result_sha256
        if terminal_result is not None:
            if terminal_sha is None:
                raise PersistenceCorruptionError("terminal result is missing its SHA-256 evidence")
            verify_payload_hash(terminal_result, terminal_sha, label="terminal run result")
            decoded = decode_terminal_result(terminal_result)
            if PersistedRunStatus.from_task_state(decoded.status) is not status:
                raise PersistenceCorruptionError(
                    "persisted run status disagrees with validated terminal result"
                )

        return PersistedRunSnapshot(
            run_id=run.id,
            project_id=project.id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
            base_commit=run.base_commit,
            status=status,
            tasks=tasks,
            evidence=evidence,
            terminal_result=terminal_result,
            terminal_result_sha256=terminal_sha,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    async def list_evidence(
        self,
        run_id: UUID,
        *,
        kind: PersistenceEvidenceKind | None = None,
    ) -> tuple[PersistedEvidence, ...]:
        async with self._session_factory() as session:
            statement = select(EvidenceRow).where(EvidenceRow.run_id == run_id)
            if kind is not None:
                statement = statement.where(EvidenceRow.kind == kind.value)
            rows = (
                await session.execute(statement.order_by(EvidenceRow.id))
            ).scalars().all()
        return tuple(self._decode_evidence_row(row) for row in rows)

    async def list_runtime_events(
        self,
        run_id: UUID,
        *,
        task_id: str | None = None,
        dispatch_id: UUID | None = None,
        kind: RuntimeEventKind | None = None,
        source: RuntimeEventSource | None = None,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit < 1 or limit > 1000:
            raise ValueError("runtime event query limit must be between 1 and 1000")
        normalized_task = self._optional_text(task_id, "task_id", max_length=128)

        async with self._session_factory() as session:
            run_exists = await session.scalar(select(RunRow.id).where(RunRow.id == run_id))
            if run_exists is None:
                raise ValueError(f"unknown persistence run: {run_id}")

            statement = select(RuntimeEventRow).where(
                RuntimeEventRow.run_id == run_id,
                RuntimeEventRow.sequence > after_sequence,
            )
            if normalized_task is not None:
                statement = statement.where(RuntimeEventRow.task_id == normalized_task)
            if dispatch_id is not None:
                statement = statement.where(RuntimeEventRow.dispatch_id == dispatch_id)
            if kind is not None:
                statement = statement.where(RuntimeEventRow.kind == kind.value)
            if source is not None:
                statement = statement.where(RuntimeEventRow.source == source.value)
            rows = (
                await session.execute(statement.order_by(RuntimeEventRow.sequence).limit(limit))
            ).scalars().all()
        return tuple(decode_runtime_event(row) for row in rows)

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
            raise ValueError(f"evidence task {task_id!r} does not belong to run {run_id}")
        return task

    @staticmethod
    def _decode_task(row: TaskRow) -> PersistedTask:
        verify_payload_hash(row.contract, row.contract_sha256, label=f"task {row.task_id}")
        try:
            task = TaskContract.model_validate(row.contract)
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted task {row.task_id!r} failed TaskContract validation: {exc}"
            ) from exc
        if task.task_id != row.task_id:
            raise PersistenceCorruptionError("persisted task id disagrees with contract payload")
        return PersistedTask(
            task=task,
            contract_sha256=row.contract_sha256,
            created_at=row.created_at,
        )

    @staticmethod
    def _decode_evidence_row(row: EvidenceRow) -> PersistedEvidence:
        verify_payload_hash(row.payload, row.payload_sha256, label=f"evidence {row.id}")
        try:
            kind = PersistenceEvidenceKind(row.kind)
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted evidence {row.id} has unknown kind {row.kind!r}"
            ) from exc
        if row.schema_version != _SCHEMA_VERSION:
            raise PersistenceCorruptionError(
                f"unsupported evidence schema version {row.schema_version} for row {row.id}"
            )
        decode_evidence(kind, row.payload)
        return PersistedEvidence(
            id=row.id,
            run_id=row.run_id,
            task_id=row.task_id,
            evidence_key=row.evidence_key,
            kind=kind,
            stage=row.stage,
            sequence=row.sequence,
            schema_version=row.schema_version,
            payload=row.payload,
            payload_sha256=row.payload_sha256,
            created_at=row.created_at,
        )

    @staticmethod
    def _same_evidence(
        row: EvidenceRow,
        *,
        task_id: str | None,
        kind: PersistenceEvidenceKind,
        stage: str | None,
        sequence: int | None,
        payload_sha256: str,
    ) -> bool:
        return (
            row.task_id == task_id
            and row.kind == kind.value
            and row.stage == stage
            and row.sequence == sequence
            and row.schema_version == _SCHEMA_VERSION
            and row.payload_sha256 == payload_sha256
        )

    @classmethod
    def _evidence_event_draft(
        cls,
        *,
        row: EvidenceRow,
        task: TaskRow | None,
        kind: PersistenceEvidenceKind,
    ) -> RuntimeEventDraft:
        source = _EVIDENCE_EVENT_SOURCES[kind]
        level = (
            RuntimeEventLevel.ERROR
            if kind is PersistenceEvidenceKind.FAILURE_REPORT
            else RuntimeEventLevel.WARNING
            if kind is PersistenceEvidenceKind.MERGE_CONFLICT
            else RuntimeEventLevel.INFO
        )
        generation = (
            task.lease_generation
            if task is not None and task.lease_generation
            else None
        )
        return RuntimeEventDraft(
            event_key=f"evidence:{row.id}",
            kind=RuntimeEventKind.EVIDENCE_RECORDED,
            source=source,
            level=level,
            task_id=row.task_id,
            dispatch_id=task.lease_dispatch_id if task is not None else None,
            generation=generation,
            message=f"Accepted {kind.value} evidence.",
            attributes={
                "evidence_id": row.id,
                "evidence_key": row.evidence_key,
                "evidence_kind": kind.value,
                "stage": row.stage,
                "evidence_sequence": row.sequence,
                "payload_sha256": row.payload_sha256,
            },
        )

    @staticmethod
    def _required_text(value: str, name: str, *, max_length: int) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must not be empty")
        if len(normalized) > max_length:
            raise ValueError(f"{name} exceeds maximum length {max_length}")
        return normalized

    @classmethod
    def _optional_text(cls, value: str | None, name: str, *, max_length: int) -> str | None:
        if value is None:
            return None
        return cls._required_text(value, name, max_length=max_length)