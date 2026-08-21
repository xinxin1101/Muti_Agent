from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.integration_gate import IntegrationGateState
from app.models.merge import MergeQueueSnapshot
from app.models.multi_run import MultiTaskRunResult
from app.models.run import TaskRunState
from app.models.scheduler import TaskScheduleState
from app.models.worker import WorkerTaskResult
from app.persistence.errors import (
    PersistenceConflictError,
    PersistenceCorruptionError,
)
from app.persistence.serialization import canonical_payload
from app.persistence.types import (
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistenceEvidenceKind,
)
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.durable_human_gate import DurableHumanGateService
from app.runtime.generation_worktrees import GenerationBoundWorktreeView
from app.runtime.merge_queue import MergeQueueError, TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


class MultiTaskDAGReader(Protocol):
    async def load_dag(self, run_id: UUID): ...


class MultiTaskEvidenceStore(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...

    async def append_evidence(self, **kwargs) -> int: ...


class MultiTaskCompletionStore(Protocol):
    async def finalize_multi_task_run(self, result: MultiTaskRunResult) -> None: ...


class MultiTaskRunReconciler(Protocol):
    async def reconcile_run(self, run_id: UUID): ...


class MultiTaskWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID): ...


class DurableMultiAgentRunController:
    """Advance one persisted DAG Run from durable facts after a worker or human decision.

    The controller is deliberately event-driven rather than a resident in-memory scheduler. Each
    invocation reconstructs task state from PostgreSQL, recovers the object-level integration ref
    from Git, persists any new integration evidence, and finally delegates newly legal dispatches
    to the accepted Step 5.4 DAG reconciler. Duplicate invocations are expected and must converge.
    """

    def __init__(
        self,
        *,
        evidence_store: MultiTaskEvidenceStore,
        dag_store: MultiTaskDAGReader,
        workspace_resolver: MultiTaskWorkspaceResolver,
        run_reconciler: MultiTaskRunReconciler,
        completion_store: MultiTaskCompletionStore,
    ) -> None:
        self._evidence_store = evidence_store
        self._dag_store = dag_store
        self._workspace_resolver = workspace_resolver
        self._run_reconciler = run_reconciler
        self._completion_store = completion_store

    async def advance(self, run_id: UUID) -> MergeQueueSnapshot | None:
        """Advance from fresh facts, retrying one benign concurrent controller race."""

        for attempt in range(2):
            try:
                return await self._advance_once(run_id)
            except (MergeQueueError, PersistenceConflictError):
                if attempt == 1:
                    raise
        raise AssertionError("bounded controller retry loop terminated unexpectedly")

    async def _advance_once(self, run_id: UUID) -> MergeQueueSnapshot | None:
        snapshot = await self._evidence_store.load_run(run_id)
        if snapshot.status is not PersistedRunStatus.RUNNING:
            return None
        persisted_dag = await self._dag_store.load_dag(run_id)
        dag = persisted_dag.dag
        if len(dag.tasks) == 1:
            return None

        executions = self._terminal_executions(snapshot)
        scheduler = self._reconstruct_scheduler(dag, executions)
        workspace = self._workspace_resolver.resolve(snapshot.project_id)
        worktrees = GenerationBoundWorktreeView(
            workspace=workspace,
            run_base_commit=snapshot.base_commit,
            executions={
                task_id: evidence
                for task_id, evidence in executions.items()
                if evidence.status is WorkerExecutionStatus.SUCCEEDED
            },
        )
        queue = TopologicalMergeQueue(
            scheduler=scheduler,
            worktrees=worktrees,  # type: ignore[arg-type]
            base_workspace=workspace,
            integration_id=f"run-{run_id.hex}",
        )

        merge_snapshot = self._integrate_available(
            scheduler=scheduler,
            queue=queue,
            worktrees=worktrees,
            executions=executions,
        )
        merge_evidence_id: int | None = None
        merge_evidence_sha256: str | None = None
        if merge_snapshot.attempts:
            merge_evidence_id, merge_evidence_sha256 = await self._persist_merge_snapshot(
                run_id=run_id,
                snapshot=merge_snapshot,
            )

        if merge_snapshot.stopped:
            conflict = GitMergeConflictClassifier(workspace).classify(merge_snapshot)
            await self._evidence_store.append_evidence(
                run_id=run_id,
                evidence_key=(
                    "integration:merge-conflict:"
                    f"{merge_snapshot.attempts[-1].sequence:04d}:"
                    f"{conflict.marker_commit[:16]}"
                ),
                kind=PersistenceEvidenceKind.MERGE_CONFLICT,
                payload_model=conflict,
                task_id=merge_snapshot.attempts[-1].task_id,
                stage="integration",
                sequence=merge_snapshot.attempts[-1].sequence,
            )
            human_gate = DurableHumanGateService(
                evidence_store=self._evidence_store,
                dag_store=self._dag_store,
                workspace_resolver=self._workspace_resolver,
            )
            gate = await human_gate.persist_live_gate(
                run_id=run_id,
                queue_snapshot=merge_snapshot,
                scheduler=scheduler,
                conflict=conflict,
                workspace=workspace,
            )
            if gate.state is IntegrationGateState.ABORTED:
                await self._finalize_human_abort_if_ready(
                    run_id=run_id,
                    dag=dag,
                    executions=executions,
                    aborted_task_id=gate.task_id,
                    abort_fingerprint=gate.evidence_fingerprint,
                    integration_head=merge_snapshot.head_commit,
                )
            # A pending or repair-authorized conflict remains intentionally paused here. A later
            # bounded repair stage may act only after revalidating this same durable gate.
            return merge_snapshot

        failed = {
            task_id
            for task_id, evidence in executions.items()
            if evidence.status is WorkerExecutionStatus.FAILED
        }
        blocked = set(dag.blocked_task_ids(failed_task_ids=failed))
        terminal = set(executions) | blocked
        order = tuple(dag.topological_order())

        if failed and terminal == set(order):
            succeeded = tuple(
                task_id
                for task_id in order
                if executions.get(task_id) is not None
                and executions[task_id].status is WorkerExecutionStatus.SUCCEEDED
            )
            result = MultiTaskRunResult(
                run_id=run_id,
                status=TaskRunState.FAILED,
                task_ids=order,
                succeeded_task_ids=succeeded,
                failed_task_ids=tuple(task_id for task_id in order if task_id in failed),
                blocked_task_ids=tuple(task_id for task_id in order if task_id in blocked),
                integration_head=(merge_snapshot.head_commit if merge_snapshot.attempts else None),
            )
            await self._completion_store.finalize_multi_task_run(result)
            return merge_snapshot

        if (
            not failed
            and set(executions) == set(order)
            and set(merge_snapshot.integrated_task_ids) == set(order)
        ):
            if merge_evidence_id is None or merge_evidence_sha256 is None:
                raise PersistenceCorruptionError(
                    "complete integration lacks persisted merge snapshot identity"
                )
            result = MultiTaskRunResult(
                run_id=run_id,
                status=TaskRunState.SUCCEEDED,
                task_ids=order,
                succeeded_task_ids=order,
                integration_head=merge_snapshot.head_commit,
                merge_evidence_id=merge_evidence_id,
                merge_evidence_sha256=merge_evidence_sha256,
            )
            await self._completion_store.finalize_multi_task_run(result)
            return merge_snapshot

        # Integration evidence is persisted before scheduling. The accepted Step 5.4 resolver will
        # therefore issue dependency tasks from an evidence-bound integration head, never merely
        # because an in-memory scheduler believes their dependencies succeeded.
        await self._run_reconciler.reconcile_run(run_id)
        return merge_snapshot

    async def _finalize_human_abort_if_ready(
        self,
        *,
        run_id: UUID,
        dag,
        executions: dict[str, WorkerExecutionEvidence],
        aborted_task_id: str,
        abort_fingerprint: str,
        integration_head: str,
    ) -> None:
        failed = {
            task_id
            for task_id, evidence in executions.items()
            if evidence.status is WorkerExecutionStatus.FAILED
        }
        failure_roots = failed | {aborted_task_id}
        blocked = set(dag.blocked_task_ids(failed_task_ids=failure_roots))
        order = tuple(dag.topological_order())
        terminal = set(executions) | blocked

        # Do not append-close the Run while an already-dispatched independent sibling may still
        # need to persist terminal worker evidence. Human ABORT stops new reconciliation dispatches
        # immediately, but finalization waits until every remaining task is terminal or derived
        # BLOCKED from failed/aborted upstream work.
        if terminal != set(order):
            return

        succeeded = tuple(
            task_id
            for task_id in order
            if task_id != aborted_task_id
            and executions.get(task_id) is not None
            and executions[task_id].status is WorkerExecutionStatus.SUCCEEDED
        )
        result = MultiTaskRunResult(
            run_id=run_id,
            status=TaskRunState.FAILED,
            task_ids=order,
            succeeded_task_ids=succeeded,
            failed_task_ids=tuple(task_id for task_id in order if task_id in failed),
            aborted_task_ids=(aborted_task_id,),
            blocked_task_ids=tuple(task_id for task_id in order if task_id in blocked),
            abort_evidence_fingerprints=(abort_fingerprint,),
            integration_head=integration_head,
        )
        await self._completion_store.finalize_multi_task_run(result)

    @staticmethod
    def _terminal_executions(
        snapshot: PersistedRunSnapshot,
    ) -> dict[str, WorkerExecutionEvidence]:
        task_ids = {item.task.task_id for item in snapshot.tasks}
        executions: dict[str, WorkerExecutionEvidence] = {}
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            try:
                evidence = WorkerExecutionEvidence.model_validate(item.payload)
            except ValueError as exc:
                raise PersistenceCorruptionError(
                    f"worker execution evidence {item.id} failed typed validation"
                ) from exc
            if evidence.run_id != snapshot.run_id or evidence.task_id not in task_ids:
                raise PersistenceCorruptionError(
                    "worker execution evidence identity disagrees with the persisted Run"
                )
            existing = executions.get(evidence.task_id)
            if existing is not None and existing != evidence:
                raise PersistenceCorruptionError(
                    f"task {evidence.task_id!r} has conflicting terminal worker evidence"
                )
            executions[evidence.task_id] = evidence
        return executions

    @staticmethod
    def _reconstruct_scheduler(dag, executions: dict[str, WorkerExecutionEvidence]) -> DAGScheduler:
        scheduler = DAGScheduler(dag)
        try:
            for task_id in dag.topological_order():
                evidence = executions.get(task_id)
                if evidence is None:
                    continue
                if scheduler.state(task_id) is not TaskScheduleState.READY:
                    raise PersistenceCorruptionError(
                        f"terminal evidence exists for non-runnable DAG task {task_id!r}"
                    )
                scheduler.start(task_id)
                if evidence.status is WorkerExecutionStatus.SUCCEEDED:
                    scheduler.succeed(task_id)
                else:
                    scheduler.fail(task_id)
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, PersistenceCorruptionError):
                raise
            raise PersistenceCorruptionError(
                f"persisted worker evidence cannot reconstruct the DAG scheduler: {exc}"
            ) from exc
        return scheduler

    @staticmethod
    def _integrate_available(
        *,
        scheduler: DAGScheduler,
        queue: TopologicalMergeQueue,
        worktrees: GenerationBoundWorktreeView,
        executions: dict[str, WorkerExecutionEvidence],
    ) -> MergeQueueSnapshot:
        for task_id in scheduler.dag.topological_order():
            if task_id in queue.integrated_task_ids:
                continue
            state = scheduler.state(task_id)
            if state in {TaskScheduleState.FAILED, TaskScheduleState.BLOCKED}:
                continue
            if state is not TaskScheduleState.SUCCEEDED:
                break
            evidence = executions[task_id]
            record = worktrees.record_for(task_id, base_commit=evidence.base_commit)
            queue.integrate(
                [
                    WorkerTaskResult(
                        task_id=task_id,
                        scheduler_state=TaskScheduleState.SUCCEEDED,
                        worktree_path=str(record.path),
                        branch_name=evidence.branch_name,
                        base_commit=evidence.base_commit,
                        commit_sha=evidence.commit_sha,
                        run_result=evidence.run_result,
                        failures=evidence.failures,
                        duration_ms=evidence.duration_ms,
                    )
                ]
            )
            if queue.snapshot().stopped:
                break
        return queue.snapshot()

    async def _persist_merge_snapshot(
        self,
        *,
        run_id: UUID,
        snapshot: MergeQueueSnapshot,
    ) -> tuple[int, str]:
        _, digest = canonical_payload(snapshot)
        evidence_id = await self._evidence_store.append_evidence(
            run_id=run_id,
            evidence_key=(
                f"integration:merge-queue:{len(snapshot.attempts):04d}:{snapshot.head_commit[:16]}"
            ),
            kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
            payload_model=snapshot,
            stage="integration",
            sequence=len(snapshot.attempts),
        )
        return evidence_id, digest
