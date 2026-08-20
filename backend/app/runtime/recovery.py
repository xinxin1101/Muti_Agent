from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from app.models.dispatch import (
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
)
from app.models.lease import TaskLeaseSnapshot, TaskLeaseState
from app.models.recovery import (
    RecoveryDisposition,
    RunRecoveryPlan,
    TaskRecoveryAssessment,
)
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistenceEvidenceKind,
)


class RecoveryRunReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class RecoveryLeaseReader(Protocol):
    async def list_task_leases(self, run_id: UUID) -> tuple[TaskLeaseSnapshot, ...]: ...


class RecoveryInspector:
    """Compose existing durable read models into a non-authoritative recovery diagnosis.

    The two reads are intentionally not treated as a mutation authorization. A future reconciler
    must perform fresh locked revalidation immediately before any takeover or enqueue operation.
    """

    def __init__(
        self,
        *,
        run_reader: RecoveryRunReader,
        lease_reader: RecoveryLeaseReader,
        classifier: RecoveryStateClassifier | None = None,
    ) -> None:
        self._run_reader = run_reader
        self._lease_reader = lease_reader
        self._classifier = classifier or RecoveryStateClassifier()

    async def inspect_run(self, run_id: UUID) -> RunRecoveryPlan:
        snapshot = await self._run_reader.load_run(run_id)
        leases = await self._lease_reader.list_task_leases(run_id)
        return self._classifier.classify(snapshot=snapshot, leases=leases)


class RecoveryStateClassifier:
    """Deterministically classify recovery state without mutating runtime authority."""

    def classify(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        leases: Sequence[TaskLeaseSnapshot],
    ) -> RunRecoveryPlan:
        task_ids = tuple(item.task.task_id for item in snapshot.tasks)
        lease_by_task = self._validate_leases(snapshot=snapshot, task_ids=task_ids, leases=leases)
        observed_at = next(iter(lease_by_task.values())).observed_at
        worker_evidence, dispatch_events = self._decode_worker_side_evidence(
            snapshot=snapshot,
            known_task_ids=set(task_ids),
        )

        assessments: list[TaskRecoveryAssessment] = []
        for task_id in task_ids:
            lease = lease_by_task[task_id]
            task_workers = worker_evidence.get(task_id, {})
            task_dispatch_events = dispatch_events.get(task_id, {})
            assessments.append(
                self._classify_task(
                    snapshot=snapshot,
                    lease=lease,
                    worker_evidence=task_workers,
                    dispatch_events=task_dispatch_events,
                )
            )

        return RunRecoveryPlan(
            run_id=snapshot.run_id,
            run_status=snapshot.status.value,
            observed_at=observed_at,
            tasks=tuple(assessments),
        )

    @staticmethod
    def _validate_leases(
        *,
        snapshot: PersistedRunSnapshot,
        task_ids: tuple[str, ...],
        leases: Sequence[TaskLeaseSnapshot],
    ) -> dict[str, TaskLeaseSnapshot]:
        lease_by_task: dict[str, TaskLeaseSnapshot] = {}
        observed_at = None
        for lease in leases:
            if lease.run_id != snapshot.run_id:
                raise PersistenceCorruptionError(
                    "recovery lease run identity disagrees with persisted Run"
                )
            if lease.task_id in lease_by_task:
                raise PersistenceCorruptionError(
                    f"recovery snapshot contains duplicate lease for task {lease.task_id!r}"
                )
            lease_by_task[lease.task_id] = lease
            if observed_at is None:
                observed_at = lease.observed_at
            elif lease.observed_at != observed_at:
                raise PersistenceCorruptionError(
                    "recovery lease snapshots must share one database observation time"
                )

        expected = set(task_ids)
        actual = set(lease_by_task)
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            raise PersistenceCorruptionError(
                "recovery lease/task identities disagree "
                f"(missing={missing}, unexpected={unexpected})"
            )
        return lease_by_task

    def _decode_worker_side_evidence(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        known_task_ids: set[str],
    ) -> tuple[
        dict[str, dict[UUID, tuple[int, WorkerExecutionEvidence]]],
        dict[str, dict[UUID, dict[WorkerDispatchPhase, int]]],
    ]:
        workers: dict[str, dict[UUID, tuple[int, WorkerExecutionEvidence]]] = {}
        dispatches: dict[str, dict[UUID, dict[WorkerDispatchPhase, int]]] = {}

        for evidence in snapshot.evidence:
            if evidence.kind not in {
                PersistenceEvidenceKind.WORKER_EXECUTION,
                PersistenceEvidenceKind.DISPATCH_EVENT,
            }:
                continue
            task_id = self._validated_task_scope(
                snapshot=snapshot,
                evidence=evidence,
                known_task_ids=known_task_ids,
            )

            if evidence.kind is PersistenceEvidenceKind.WORKER_EXECUTION:
                execution = WorkerExecutionEvidence.model_validate(evidence.payload)
                self._validate_payload_identity(
                    snapshot=snapshot,
                    evidence=evidence,
                    payload_run_id=execution.run_id,
                    payload_task_id=execution.task_id,
                )
                by_dispatch = workers.setdefault(task_id, {})
                if execution.dispatch_id in by_dispatch:
                    raise PersistenceCorruptionError(
                        "multiple terminal WORKER_EXECUTION evidence rows exist for one dispatch "
                        f"{execution.dispatch_id}"
                    )
                by_dispatch[execution.dispatch_id] = (evidence.id, execution)
                continue

            dispatch = WorkerDispatchEvent.model_validate(evidence.payload)
            self._validate_payload_identity(
                snapshot=snapshot,
                evidence=evidence,
                payload_run_id=dispatch.run_id,
                payload_task_id=dispatch.task_id,
            )
            by_dispatch = dispatches.setdefault(task_id, {}).setdefault(dispatch.dispatch_id, {})
            if dispatch.phase in by_dispatch:
                raise PersistenceCorruptionError(
                    "duplicate worker dispatch phase evidence exists for dispatch "
                    f"{dispatch.dispatch_id}: {dispatch.phase.value}"
                )
            by_dispatch[dispatch.phase] = evidence.id

        for task_id, task_dispatches in dispatches.items():
            task_workers = workers.get(task_id, {})
            for dispatch_id, phases in task_dispatches.items():
                if (
                    WorkerDispatchPhase.COMPLETED in phases
                    and dispatch_id not in task_workers
                ):
                    raise PersistenceCorruptionError(
                        "COMPLETED dispatch evidence exists without terminal WORKER_EXECUTION "
                        f"evidence for dispatch {dispatch_id}"
                    )
        return workers, dispatches

    @staticmethod
    def _validated_task_scope(
        *,
        snapshot: PersistedRunSnapshot,
        evidence: PersistedEvidence,
        known_task_ids: set[str],
    ) -> str:
        if evidence.run_id != snapshot.run_id:
            raise PersistenceCorruptionError(
                f"persisted evidence {evidence.id} disagrees with recovery Run identity"
            )
        if evidence.task_id is None or evidence.task_id not in known_task_ids:
            raise PersistenceCorruptionError(
                f"worker-side evidence {evidence.id} is not bound to a known persisted task"
            )
        return evidence.task_id

    @staticmethod
    def _validate_payload_identity(
        *,
        snapshot: PersistedRunSnapshot,
        evidence: PersistedEvidence,
        payload_run_id: UUID,
        payload_task_id: str,
    ) -> None:
        if payload_run_id != snapshot.run_id or payload_run_id != evidence.run_id:
            raise PersistenceCorruptionError(
                f"worker-side evidence {evidence.id} payload Run identity mismatch"
            )
        if payload_task_id != evidence.task_id:
            raise PersistenceCorruptionError(
                f"worker-side evidence {evidence.id} payload Task identity mismatch"
            )

    def _classify_task(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        lease: TaskLeaseSnapshot,
        worker_evidence: dict[UUID, tuple[int, WorkerExecutionEvidence]],
        dispatch_events: dict[UUID, dict[WorkerDispatchPhase, int]],
    ) -> TaskRecoveryAssessment:
        if snapshot.status is not PersistedRunStatus.RUNNING:
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.NO_ACTION_RUN_TERMINAL,
                reason="Persisted Run is terminal; recovery must not reopen it.",
            )

        if lease.state is TaskLeaseState.UNOWNED:
            if worker_evidence or dispatch_events:
                raise PersistenceCorruptionError(
                    f"UNOWNED task {lease.task_id!r} contains worker-side evidence"
                )
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.BLOCKED_UNOWNED_DISPATCH_AMBIGUITY,
                reason=(
                    "Task is UNOWNED, but V1.0 has no durable dispatcher-intent ledger to prove "
                    "whether a broker message is absent or merely awaiting lease acquisition."
                ),
            )

        assert lease.dispatch_id is not None
        current_worker = worker_evidence.get(lease.dispatch_id)

        if lease.state is TaskLeaseState.ACTIVE:
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.WAIT_ACTIVE_OWNER,
                worker=current_worker,
                reason=(
                    "Current lease generation is ACTIVE under database time; recovery must not "
                    "race the live fenced owner."
                ),
            )

        if current_worker is not None:
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.RESUME_FROM_TERMINAL_EVIDENCE,
                worker=current_worker,
                reason=(
                    "Accepted terminal WORKER_EXECUTION evidence already exists for the current "
                    "dispatch; downstream recovery should resume from that evidence instead of "
                    "rerunning agent/tool execution."
                ),
            )

        if lease.state is TaskLeaseState.EXPIRED:
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.REDISPATCH_CANDIDATE_EXPIRED_GENERATION,
                reason=(
                    "Current generation is EXPIRED and has no terminal WORKER_EXECUTION evidence. "
                    "This is a redispatch candidate only; mutation still requires fresh locked "
                    "revalidation."
                ),
            )

        if lease.state is TaskLeaseState.RELEASED:
            return self._assessment(
                snapshot=snapshot,
                lease=lease,
                disposition=RecoveryDisposition.BLOCKED_RELEASED_EVIDENCE_GAP,
                reason=(
                    "Lease ownership was RELEASED without terminal WORKER_EXECUTION evidence. "
                    "Released generations are durable ownership history and cannot be silently "
                    "reacquired by recovery."
                ),
            )

        raise PersistenceCorruptionError(
            f"unsupported task lease state in recovery classifier: {lease.state.value}"
        )

    @staticmethod
    def _assessment(
        *,
        snapshot: PersistedRunSnapshot,
        lease: TaskLeaseSnapshot,
        disposition: RecoveryDisposition,
        reason: str,
        worker: tuple[int, WorkerExecutionEvidence] | None = None,
    ) -> TaskRecoveryAssessment:
        evidence_id: int | None = None
        execution = None
        if worker is not None:
            evidence_id, execution = worker
        return TaskRecoveryAssessment(
            run_id=snapshot.run_id,
            task_id=lease.task_id,
            disposition=disposition,
            lease_state=lease.state,
            lease_generation=lease.generation,
            lease_dispatch_id=lease.dispatch_id,
            observed_at=lease.observed_at,
            worker_execution_status=(execution.status if execution is not None else None),
            worker_execution_evidence_id=evidence_id,
            reason=reason,
        )
