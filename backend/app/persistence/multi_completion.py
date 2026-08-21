from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.sql import func

from app.models.dag import TaskDAG, TaskNode
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.events import (
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.models.merge import MergeQueueSnapshot
from app.models.multi_run import MultiTaskRunResult
from app.models.run import TaskRunState
from app.models.task import TaskContract
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.events import append_runtime_event
from app.persistence.models import EvidenceRow, TaskRow
from app.persistence.repository import PostgresEvidenceStore
from app.persistence.serialization import canonical_payload, verify_payload_hash
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


class PostgresMultiTaskCompletionStore(PostgresEvidenceStore):
    """Finalize a DAG Run only after fresh PostgreSQL evidence revalidation."""

    async def finalize_multi_task_run(self, result: MultiTaskRunResult) -> None:
        payload, digest = canonical_payload(result)
        persisted_status = PersistedRunStatus.from_task_state(result.status)

        async with self._session_factory.begin() as session:
            run = await self._locked_run(session, result.run_id)
            rows = (
                await session.execute(
                    select(TaskRow)
                    .where(TaskRow.run_id == result.run_id)
                    .order_by(TaskRow.task_id)
                    .with_for_update()
                )
            ).scalars().all()
            dag = self._decode_dag(rows)
            if tuple(dag.topological_order()) != result.task_ids:
                raise PersistenceConflictError(
                    "multi-task terminal result does not match the persisted DAG identity"
                )

            if run.status != PersistedRunStatus.RUNNING.value:
                if (
                    run.status == persisted_status.value
                    and run.terminal_result_sha256 == digest
                    and run.terminal_result == payload
                ):
                    return
                raise PersistenceConflictError(
                    "persisted DAG Run was already finalized with different terminal evidence"
                )

            evidence_rows = (
                await session.execute(
                    select(EvidenceRow)
                    .where(EvidenceRow.run_id == result.run_id)
                    .order_by(EvidenceRow.id)
                    .with_for_update()
                )
            ).scalars().all()
            worker_evidence = self._worker_evidence(evidence_rows, result.run_id)

            if result.status is TaskRunState.SUCCEEDED:
                self._validate_success(
                    result=result,
                    dag=dag,
                    evidence_rows=evidence_rows,
                    worker_evidence=worker_evidence,
                )
            else:
                self._validate_failure(
                    result=result,
                    dag=dag,
                    worker_evidence=worker_evidence,
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
                    message=f"Persisted DAG Run finalized as {persisted_status.value}.",
                    attributes={
                        "status": persisted_status.value,
                        "terminal_result_sha256": digest,
                        "task_count": len(result.task_ids),
                    },
                ),
            )

    @staticmethod
    def _decode_dag(rows: list[TaskRow]) -> TaskDAG:
        if len(rows) < 2:
            raise PersistenceConflictError(
                "MultiTaskRunResult can finalize only a persisted multi-task Run"
            )
        nodes: list[TaskNode] = []
        for row in rows:
            verify_payload_hash(
                row.contract,
                row.contract_sha256,
                label=f"task contract {row.task_id}",
            )
            try:
                task = TaskContract.model_validate(row.contract)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"persisted task contract {row.task_id!r} failed validation"
                ) from exc
            if task.task_id != row.task_id:
                raise PersistenceCorruptionError(
                    "persisted task row identity disagrees with its contract"
                )
            nodes.append(
                TaskNode(
                    task=task,
                    depends_on=tuple(row.depends_on or ()),
                )
            )
        return TaskDAG(tasks=tuple(nodes))

    @staticmethod
    def _worker_evidence(
        rows: list[EvidenceRow],
        run_id: UUID,
    ) -> dict[str, WorkerExecutionEvidence]:
        decoded: dict[str, WorkerExecutionEvidence] = {}
        for row in rows:
            if row.kind != PersistenceEvidenceKind.WORKER_EXECUTION.value:
                continue
            verify_payload_hash(
                row.payload,
                row.payload_sha256,
                label=f"worker execution evidence {row.id}",
            )
            try:
                item = WorkerExecutionEvidence.model_validate(row.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"worker execution evidence {row.id} failed validation"
                ) from exc
            if item.run_id != run_id or row.task_id != item.task_id:
                raise PersistenceCorruptionError(
                    "worker execution evidence identity disagrees with persistence row"
                )
            existing = decoded.get(item.task_id)
            if existing is not None and existing != item:
                raise PersistenceCorruptionError(
                    f"task {item.task_id!r} has conflicting terminal worker evidence"
                )
            decoded[item.task_id] = item
        return decoded

    @staticmethod
    def _validate_success(
        *,
        result: MultiTaskRunResult,
        dag: TaskDAG,
        evidence_rows: list[EvidenceRow],
        worker_evidence: dict[str, WorkerExecutionEvidence],
    ) -> None:
        if set(worker_evidence) != set(dag.task_ids):
            raise PersistenceConflictError(
                "successful DAG finalization requires terminal worker evidence for every task"
            )
        if any(
            item.status is not WorkerExecutionStatus.SUCCEEDED
            for item in worker_evidence.values()
        ):
            raise PersistenceConflictError(
                "successful DAG finalization cannot include failed worker evidence"
            )
        if result.merge_evidence_id is None or result.merge_evidence_sha256 is None:
            raise PersistenceConflictError(
                "successful DAG finalization requires merge evidence identity"
            )
        row = next(
            (item for item in evidence_rows if item.id == result.merge_evidence_id),
            None,
        )
        if row is None or row.kind != PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT.value:
            raise PersistenceConflictError(
                "successful DAG finalization references unavailable merge evidence"
            )
        if row.payload_sha256 != result.merge_evidence_sha256:
            raise PersistenceConflictError(
                "successful DAG finalization merge digest does not match persistence"
            )
        verify_payload_hash(row.payload, row.payload_sha256, label="terminal merge snapshot")
        try:
            merge = MergeQueueSnapshot.model_validate(row.payload)
        except ValidationError as exc:
            raise PersistenceCorruptionError(
                "terminal merge queue evidence failed validation"
            ) from exc
        if merge.stopped or set(merge.integrated_task_ids) != set(dag.task_ids):
            raise PersistenceConflictError(
                "successful DAG finalization requires every task to be integrated"
            )
        if merge.head_commit != result.integration_head:
            raise PersistenceConflictError(
                "successful DAG finalization integration head changed"
            )
        attempts = {item.task_id: item for item in merge.attempts}
        for task_id, execution in worker_evidence.items():
            attempt = attempts.get(task_id)
            if (
                attempt is None
                or execution.commit_sha is None
                or attempt.task_base_commit != execution.base_commit
                or attempt.task_commit != execution.commit_sha
            ):
                raise PersistenceConflictError(
                    f"merge evidence for {task_id!r} does not match worker terminal evidence"
                )

    @staticmethod
    def _validate_failure(
        *,
        result: MultiTaskRunResult,
        dag: TaskDAG,
        worker_evidence: dict[str, WorkerExecutionEvidence],
    ) -> None:
        failed = set(result.failed_task_ids)
        expected_blocked = set(dag.blocked_task_ids(failed_task_ids=failed))
        if expected_blocked != set(result.blocked_task_ids):
            raise PersistenceConflictError(
                "failed DAG terminal result does not match dependency-derived blocked tasks"
            )
        for task_id in result.failed_task_ids:
            execution = worker_evidence.get(task_id)
            if execution is None or execution.status is not WorkerExecutionStatus.FAILED:
                raise PersistenceConflictError(
                    f"failed DAG task {task_id!r} lacks failed worker evidence"
                )
        for task_id in result.succeeded_task_ids:
            execution = worker_evidence.get(task_id)
            if execution is None or execution.status is not WorkerExecutionStatus.SUCCEEDED:
                raise PersistenceConflictError(
                    f"successful DAG task {task_id!r} lacks successful worker evidence"
                )
