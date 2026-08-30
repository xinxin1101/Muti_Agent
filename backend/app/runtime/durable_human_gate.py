from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.conflict import MergeConflictEvidence
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.integration_gate import (
    HumanGateDecision,
    IntegrationGateSnapshot,
    IntegrationPolicyRoute,
)
from app.models.merge import MergeQueueSnapshot
from app.models.scheduler import TaskScheduleState
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus, PersistenceEvidenceKind
from app.runtime.integration_gate import IntegrationHumanGate, IntegrationHumanGateError
from app.runtime.scheduler import DAGScheduler


class DurableHumanGateEvidenceStore(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...

    async def append_evidence(self, **kwargs) -> int: ...


class DurableHumanGateDAGStore(Protocol):
    async def load_dag(self, run_id: UUID): ...


class DurableHumanGateWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID): ...


class DurableHumanGateService:
    """Persist and resume IntegrationHumanGate decisions from durable PostgreSQL/Git facts.

    PostgreSQL evidence makes a pending gate discoverable after process restarts. The existing Git
    decision ref remains the action binding: every decision is recorded only after revalidating the
    exact integration head, conflict marker, task branch/commit, policy and evidence fingerprint.
    """

    def __init__(
        self,
        *,
        evidence_store: DurableHumanGateEvidenceStore,
        dag_store: DurableHumanGateDAGStore,
        workspace_resolver: DurableHumanGateWorkspaceResolver,
    ) -> None:
        self._evidence_store = evidence_store
        self._dag_store = dag_store
        self._workspace_resolver = workspace_resolver

    async def list_gates(self, run_id: UUID) -> tuple[IntegrationGateSnapshot, ...]:
        snapshot = await self._evidence_store.load_run(run_id)
        latest: dict[str, tuple[int, IntegrationGateSnapshot]] = {}
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.INTEGRATION_GATE:
                continue
            try:
                gate = IntegrationGateSnapshot.model_validate(item.payload)
            except ValueError as exc:
                raise PersistenceCorruptionError(
                    f"integration gate evidence {item.id} failed typed validation"
                ) from exc
            existing = latest.get(gate.evidence_fingerprint)
            if existing is None or item.id > existing[0]:
                latest[gate.evidence_fingerprint] = (item.id, gate)
        return tuple(
            item[1]
            for item in sorted(
                latest.values(),
                key=lambda value: value[0],
            )
        )

    async def decide(
        self,
        *,
        run_id: UUID,
        task_id: str,
        evidence_fingerprint: str,
        decision: HumanGateDecision,
        note: str = "",
        actor: str = "product-user",
    ) -> IntegrationGateSnapshot:
        snapshot = await self._evidence_store.load_run(run_id)
        if snapshot.status is not PersistedRunStatus.RUNNING:
            raise PersistenceConflictError("terminal Runs cannot accept new human decisions")

        persisted_dag = await self._dag_store.load_dag(run_id)
        scheduler = self._reconstruct_scheduler(snapshot, persisted_dag.dag)
        queue_snapshot, conflict = self._current_conflict(
            snapshot=snapshot,
            task_id=task_id,
        )
        workspace = self._workspace_resolver.resolve(snapshot.project_id)
        try:
            gate = IntegrationHumanGate(
                workspace=workspace,
                queue_snapshot=queue_snapshot,
                scheduler=scheduler,
                evidence=conflict,
            )
            live = gate.snapshot()
        except IntegrationHumanGateError as exc:
            raise PersistenceConflictError(
                "human gate preconditions no longer match current Git/policy evidence"
            ) from exc

        if live.evidence_fingerprint != evidence_fingerprint:
            raise PersistenceConflictError(
                "human decision references a stale conflict evidence fingerprint"
            )
        if live.task_id != task_id:
            raise PersistenceConflictError("human decision task identity changed")
        if live.policy.route is not IntegrationPolicyRoute.HUMAN_REQUIRED:
            raise PersistenceConflictError(
                "human decision is not accepted for an automatic repair policy route"
            )

        if live.human_decision is None:
            try:
                decided = gate.record_human_decision(
                    decision,
                    actor=actor,
                    note=note,
                )
            except IntegrationHumanGateError as exc:
                raise PersistenceConflictError(
                    "human decision could not be committed against current Git authority"
                ) from exc
        else:
            # Crash-safe DB catch-up: the Git decision may exist even if the process died before
            # PostgreSQL evidence was appended. The same decision is accepted idempotently;
            # conflicting second decisions fail closed.
            if live.human_decision.decision is not decision:
                raise PersistenceConflictError(
                    "a different human decision is already bound to this conflict"
                )
            decided = live

        human_decision = decided.human_decision
        if human_decision is None:
            raise PersistenceCorruptionError("decided integration gate lacks human decision")
        sequence = queue_snapshot.attempts[-1].sequence
        await self._evidence_store.append_evidence(
            run_id=run_id,
            evidence_key=f"integration:human-decision:{decided.evidence_fingerprint[:40]}",
            kind=PersistenceEvidenceKind.HUMAN_DECISION,
            payload_model=human_decision,
            task_id=task_id,
            stage="integration",
            sequence=sequence,
        )
        await self._persist_gate(
            run_id=run_id,
            gate=decided,
            sequence=sequence,
            suffix="decided",
        )
        return decided

    async def persist_live_gate(
        self,
        *,
        run_id: UUID,
        queue_snapshot: MergeQueueSnapshot,
        scheduler: DAGScheduler,
        conflict: MergeConflictEvidence,
        workspace,
    ) -> IntegrationGateSnapshot:
        try:
            gate = IntegrationHumanGate(
                workspace=workspace,
                queue_snapshot=queue_snapshot,
                scheduler=scheduler,
                evidence=conflict,
            ).snapshot()
        except IntegrationHumanGateError as exc:
            raise PersistenceCorruptionError(
                "current merge conflict cannot be reproduced by the integration gate"
            ) from exc
        sequence = queue_snapshot.attempts[-1].sequence
        suffix = "pending" if gate.human_decision is None else "decided"
        await self._persist_gate(
            run_id=run_id,
            gate=gate,
            sequence=sequence,
            suffix=suffix,
        )
        return gate

    async def _persist_gate(
        self,
        *,
        run_id: UUID,
        gate: IntegrationGateSnapshot,
        sequence: int,
        suffix: str,
    ) -> None:
        await self._evidence_store.append_evidence(
            run_id=run_id,
            evidence_key=(f"integration:gate:{gate.evidence_fingerprint[:40]}:{suffix}"),
            kind=PersistenceEvidenceKind.INTEGRATION_GATE,
            payload_model=gate,
            task_id=gate.task_id,
            stage="integration",
            sequence=sequence,
        )

    @staticmethod
    def _current_conflict(
        *,
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> tuple[MergeQueueSnapshot, MergeConflictEvidence]:
        queue: MergeQueueSnapshot | None = None
        conflict: MergeConflictEvidence | None = None
        for item in reversed(snapshot.evidence):
            if queue is None and item.kind is PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT:
                candidate = MergeQueueSnapshot.model_validate(item.payload)
                if (
                    candidate.stopped
                    and candidate.attempts
                    and candidate.attempts[-1].task_id == task_id
                ):
                    queue = candidate
            elif conflict is None and item.kind is PersistenceEvidenceKind.MERGE_CONFLICT:
                candidate = MergeConflictEvidence.model_validate(item.payload)
                if item.task_id == task_id:
                    conflict = candidate
            if queue is not None and conflict is not None:
                break
        if queue is None or conflict is None:
            raise PersistenceConflictError(
                f"task {task_id!r} has no durable stopped integration conflict"
            )
        terminal = queue.attempts[-1]
        if (
            conflict.integration_head != queue.head_commit
            or conflict.task_commit != terminal.task_commit
        ):
            raise PersistenceCorruptionError(
                "persisted conflict evidence does not bind the stopped merge queue"
            )
        return queue, conflict

    @staticmethod
    def _reconstruct_scheduler(snapshot: PersistedRunSnapshot, dag) -> DAGScheduler:
        executions: dict[str, WorkerExecutionEvidence] = {}
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            try:
                execution = WorkerExecutionEvidence.model_validate(item.payload)
            except ValueError as exc:
                raise PersistenceCorruptionError(
                    f"worker execution evidence {item.id} failed typed validation"
                ) from exc
            existing = executions.get(execution.task_id)
            if existing is not None and existing != execution:
                raise PersistenceCorruptionError(
                    f"task {execution.task_id!r} has conflicting terminal worker evidence"
                )
            executions[execution.task_id] = execution

        scheduler = DAGScheduler(dag)
        try:
            for task_id in dag.topological_order():
                execution = executions.get(task_id)
                if execution is None:
                    continue
                if scheduler.state(task_id) is not TaskScheduleState.READY:
                    raise PersistenceCorruptionError(
                        f"terminal evidence exists for non-runnable DAG task {task_id!r}"
                    )
                scheduler.start(task_id)
                if execution.status is WorkerExecutionStatus.SUCCEEDED:
                    scheduler.succeed(task_id)
                else:
                    scheduler.fail(task_id)
        except (RuntimeError, ValueError) as exc:
            if isinstance(exc, PersistenceCorruptionError):
                raise
            raise PersistenceCorruptionError(
                f"persisted evidence cannot reconstruct human-gate scheduler: {exc}"
            ) from exc
        return scheduler
