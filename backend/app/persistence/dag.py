from __future__ import annotations

import re
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.dag import TaskDAG, TaskNode
from app.models.events import (
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventSource,
)
from app.models.task import TaskContract
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.events import append_runtime_event
from app.persistence.models import ProjectRow, RunRow, TaskRow
from app.persistence.serialization import canonical_payload, verify_payload_hash
from app.persistence.types import PersistedRunStatus

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class PersistenceDAGUnavailableError(RuntimeError):
    """Raised when a multi-task Run predates authoritative DAG persistence."""


class PersistedDAGSource(StrEnum):
    PERSISTED = "PERSISTED"
    IMPLICIT_SINGLE_TASK = "IMPLICIT_SINGLE_TASK"


class PersistedDAGSnapshot(BaseModel):
    """Validated immutable DAG read model for product projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    dag: TaskDAG
    dag_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: PersistedDAGSource


class PostgresDAGStore:
    """Immutable persistence boundary for the validated TaskDAG topology.

    A NULL ``tasks.depends_on`` / ``runs.dag_sha256`` pair means topology was never
    accepted into Step 4.4 persistence. Multi-task topology is never synthesized from
    task order or browser state. Once recorded, a DAG is immutable and idempotent.
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
    ) -> PostgresDAGStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def start_run(
        self,
        *,
        project_id: UUID,
        dag: TaskDAG,
        base_commit: str,
        run_id: UUID | None = None,
    ) -> UUID:
        """Atomically persist Run identity, tasks, topology/hash, and RUN_STARTED."""

        commit = base_commit.strip().lower()
        if _COMMIT_RE.fullmatch(commit) is None:
            raise ValueError("base_commit must be a 40-64 character lowercase Git object id")

        normalized = normalize_task_dag(dag)
        _, dag_digest = canonical_payload(normalized)
        nodes = {node.task.task_id: node for node in normalized.tasks}
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
                    dag_sha256=dag_digest,
                )
                session.add(run)
                for task_id in normalized.topological_order():
                    node = nodes[task_id]
                    payload, contract_digest = canonical_payload(node.task)
                    session.add(
                        TaskRow(
                            run_id=candidate_run_id,
                            task_id=task_id,
                            contract=payload,
                            contract_sha256=contract_digest,
                            depends_on=list(node.depends_on),
                            node_metadata=_node_metadata(node),
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
                            "task_count": len(normalized.tasks),
                            "dag_sha256": dag_digest,
                        },
                    ),
                )
        except IntegrityError as exc:
            raise PersistenceConflictError(
                "run identity already exists or violates DAG persistence constraints: "
                f"{candidate_run_id}"
            ) from exc

        return candidate_run_id

    async def persist_dag(self, *, run_id: UUID, dag: TaskDAG) -> PersistedDAGSnapshot:
        """Freeze topology for a still-running legacy Run that has no accepted DAG yet."""

        normalized = normalize_task_dag(dag)
        _, digest = canonical_payload(normalized)

        async with self._session_factory.begin() as session:
            run = (
                await session.execute(select(RunRow).where(RunRow.id == run_id).with_for_update())
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"unknown persistence run: {run_id}")

            rows = (
                (
                    await session.execute(
                        select(TaskRow)
                        .where(TaskRow.run_id == run_id)
                        .order_by(TaskRow.task_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            self._validate_task_identity(rows, normalized)

            if run.dag_sha256 is not None:
                existing = self._decode_persisted_dag(
                    run_id=run_id,
                    rows=rows,
                    digest=run.dag_sha256,
                )
                _, existing_digest = canonical_payload(existing)
                if existing_digest != digest:
                    raise PersistenceConflictError(
                        "persisted Run DAG is immutable and differs from the proposed topology"
                    )
                return PersistedDAGSnapshot(
                    run_id=run_id,
                    dag=existing,
                    dag_sha256=run.dag_sha256,
                    source=PersistedDAGSource.PERSISTED,
                )

            if any(row.depends_on is not None for row in rows):
                raise PersistenceCorruptionError(
                    "task dependency rows exist without the Run DAG integrity hash"
                )
            if run.status != PersistedRunStatus.RUNNING.value:
                raise PersistenceConflictError(
                    "authoritative DAG topology cannot be added after a Run is terminal"
                )

            nodes = {node.task.task_id: node for node in normalized.tasks}
            for row in rows:
                row.depends_on = list(nodes[row.task_id].depends_on)
                row.node_metadata = _node_metadata(nodes[row.task_id])
            run.dag_sha256 = digest
            await session.flush()

        return PersistedDAGSnapshot(
            run_id=run_id,
            dag=normalized,
            dag_sha256=digest,
            source=PersistedDAGSource.PERSISTED,
        )

    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot:
        async with self._session_factory() as session:
            run = await session.scalar(select(RunRow).where(RunRow.id == run_id))
            if run is None:
                raise ValueError(f"unknown persistence run: {run_id}")
            rows = (
                (
                    await session.execute(
                        select(TaskRow).where(TaskRow.run_id == run_id).order_by(TaskRow.task_id)
                    )
                )
                .scalars()
                .all()
            )

        if not rows:
            raise PersistenceCorruptionError("persisted Run contains no tasks")

        if run.dag_sha256 is None:
            if len(rows) != 1:
                raise PersistenceDAGUnavailableError(
                    "authoritative DAG topology is unavailable for this historical multi-task Run"
                )
            if rows[0].depends_on is not None:
                raise PersistenceCorruptionError(
                    "task dependency rows exist without the Run DAG integrity hash"
                )
            task = _decode_task_contract(rows[0])
            dag = TaskDAG(tasks=(TaskNode(task=task, depends_on=()),))
            normalized = normalize_task_dag(dag)
            _, digest = canonical_payload(normalized)
            return PersistedDAGSnapshot(
                run_id=run_id,
                dag=normalized,
                dag_sha256=digest,
                source=PersistedDAGSource.IMPLICIT_SINGLE_TASK,
            )

        dag = self._decode_persisted_dag(
            run_id=run_id,
            rows=rows,
            digest=run.dag_sha256,
        )
        return PersistedDAGSnapshot(
            run_id=run_id,
            dag=dag,
            dag_sha256=run.dag_sha256,
            source=PersistedDAGSource.PERSISTED,
        )

    @staticmethod
    def _validate_task_identity(rows: list[TaskRow], dag: TaskDAG) -> None:
        row_ids = [row.task_id for row in rows]
        dag_ids = sorted(dag.task_ids)
        if row_ids != dag_ids:
            raise PersistenceConflictError(
                "proposed DAG task ids must exactly match the persisted Run tasks"
            )

        nodes = {node.task.task_id: node for node in dag.tasks}
        for row in rows:
            _, digest = canonical_payload(nodes[row.task_id].task)
            if digest != row.contract_sha256:
                raise PersistenceConflictError(
                    f"proposed DAG task contract differs from persisted task {row.task_id!r}"
                )

    @staticmethod
    def _decode_persisted_dag(
        *,
        run_id: UUID,
        rows: list[TaskRow],
        digest: str,
    ) -> TaskDAG:
        if any(row.depends_on is None for row in rows):
            raise PersistenceCorruptionError(
                "Run DAG integrity hash exists but one or more dependency rows are missing"
            )
        try:
            dag = TaskDAG(
                tasks=tuple(
                    TaskNode(
                        task=_decode_task_contract(row),
                        depends_on=tuple(row.depends_on or ()),
                        **_decode_node_metadata(row),
                    )
                    for row in rows
                )
            )
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted Run DAG failed TaskDAG validation for {run_id}: {exc}"
            ) from exc
        normalized = normalize_task_dag(dag)
        payload, _ = canonical_payload(normalized)
        try:
            verify_payload_hash(payload, digest, label=f"Run {run_id} DAG")
        except PersistenceCorruptionError:
            # DAGs written before 0013 did not contain node metadata.  Preserve their
            # accepted topology while making new runs hash the richer node payload.
            if any(row.node_metadata is not None for row in rows):
                raise
            legacy_payload = {"tasks": []}
            for item in payload["tasks"]:
                legacy_payload["tasks"].append(
                    {
                        key: value
                        for key, value in item.items()
                        if key
                        not in {
                            "execution_mode",
                            "produces",
                            "consumes",
                            "complexity",
                            "budget_allocation",
                            "complexity_assessment",
                        }
                    }
                )
            verify_payload_hash(legacy_payload, digest, label=f"legacy Run {run_id} DAG")
        return normalized


def _decode_task_contract(row: TaskRow) -> TaskContract:
    verify_payload_hash(row.contract, row.contract_sha256, label=f"task {row.task_id}")
    try:
        task = TaskContract.model_validate(row.contract)
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted task {row.task_id!r} failed TaskContract validation: {exc}"
        ) from exc
    if task.task_id != row.task_id:
        raise PersistenceCorruptionError("persisted task id disagrees with contract payload")
    return task


def normalize_task_dag(dag: TaskDAG) -> TaskDAG:
    """Return one deterministic representation so the DAG hash ignores input ordering."""

    return TaskDAG(
        tasks=tuple(
            TaskNode(
                task=dag.node(task_id).task,
                depends_on=tuple(sorted(dag.node(task_id).depends_on)),
                execution_mode=dag.node(task_id).execution_mode,
                produces=dag.node(task_id).produces,
                consumes=dag.node(task_id).consumes,
                complexity=dag.node(task_id).complexity,
                budget_allocation=dag.node(task_id).budget_allocation,
                complexity_assessment=dag.node(task_id).complexity_assessment,
            )
            for task_id in dag.topological_order()
        )
    )


def _node_metadata(node: TaskNode) -> dict | None:
    """Persist non-contract DAG facts separately from the immutable task contract."""

    payload = {
        "execution_mode": node.execution_mode.value,
        "produces": list(node.produces),
        "consumes": list(node.consumes),
        "complexity": node.complexity.value if node.complexity is not None else None,
        "budget_allocation": (
            node.budget_allocation.model_dump(mode="json")
            if node.budget_allocation is not None
            else None
        ),
        "complexity_assessment": (
            node.complexity_assessment.model_dump(mode="json")
            if node.complexity_assessment is not None
            else None
        ),
    }
    return payload if any(value not in (None, [], "AGENT") for value in payload.values()) else None


def _decode_node_metadata(row: TaskRow) -> dict:
    if row.node_metadata is None:
        return {}
    try:
        payload = dict(row.node_metadata)
        return {
            "execution_mode": payload.get("execution_mode", "AGENT"),
            "produces": tuple(payload.get("produces", ())),
            "consumes": tuple(payload.get("consumes", ())),
            "complexity": payload.get("complexity"),
            "budget_allocation": payload.get("budget_allocation"),
            "complexity_assessment": payload.get("complexity_assessment"),
        }
    except (TypeError, ValueError) as exc:
        raise PersistenceCorruptionError(
            f"persisted task {row.task_id!r} has invalid DAG node metadata: {exc}"
        ) from exc
