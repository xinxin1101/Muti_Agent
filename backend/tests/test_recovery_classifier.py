from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.models import (
    RecoveryDisposition,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskLeaseSnapshot,
    TaskLeaseState,
    TaskRunState,
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.runtime import RecoveryStateClassifier

_NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
_BASE_COMMIT = "a" * 40


def _task(task_id: str = "RECOVERY-1") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Classify one persisted recovery state.",
        readable_files=["src/**"],
        writable_files=["src/**"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Recovery state is derived from durable evidence."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _persisted_task(task_id: str = "RECOVERY-1") -> PersistedTask:
    return PersistedTask(
        task=_task(task_id),
        contract_sha256="1" * 64,
        created_at=_NOW - timedelta(minutes=2),
    )


def _run(
    *,
    run_id: UUID | None = None,
    task_ids: tuple[str, ...] = ("RECOVERY-1",),
    status: PersistedRunStatus = PersistedRunStatus.RUNNING,
    evidence: tuple[PersistedEvidence, ...] = (),
) -> PersistedRunSnapshot:
    resolved_run_id = run_id or uuid4()
    terminal = status is not PersistedRunStatus.RUNNING
    return PersistedRunSnapshot(
        run_id=resolved_run_id,
        project_id=uuid4(),
        repository_url="https://github.com/example/recovery-fixture.git",
        default_branch="main",
        base_commit=_BASE_COMMIT,
        status=status,
        tasks=tuple(_persisted_task(task_id) for task_id in task_ids),
        evidence=evidence,
        terminal_result={"status": status.value} if terminal else None,
        terminal_result_sha256="2" * 64 if terminal else None,
        started_at=_NOW - timedelta(minutes=1),
        finished_at=_NOW if terminal else None,
    )


def _lease(
    *,
    run_id: UUID,
    task_id: str = "RECOVERY-1",
    state: TaskLeaseState,
    dispatch_id: UUID | None = None,
    observed_at: datetime = _NOW,
    generation: int = 1,
) -> TaskLeaseSnapshot:
    if state is TaskLeaseState.UNOWNED:
        return TaskLeaseSnapshot(
            run_id=run_id,
            task_id=task_id,
            state=state,
            generation=0,
            observed_at=observed_at,
        )

    resolved_dispatch = dispatch_id or uuid4()
    acquired_at = observed_at - timedelta(seconds=30)
    heartbeat_at = observed_at - timedelta(seconds=10)
    if state is TaskLeaseState.EXPIRED:
        lease_until = observed_at - timedelta(seconds=1)
    else:
        lease_until = observed_at + timedelta(seconds=30)
    released_at = observed_at - timedelta(seconds=2) if state is TaskLeaseState.RELEASED else None
    return TaskLeaseSnapshot(
        run_id=run_id,
        task_id=task_id,
        state=state,
        generation=generation,
        owner_id="worker-recovery",
        dispatch_id=resolved_dispatch,
        acquired_at=acquired_at,
        heartbeat_at=heartbeat_at,
        lease_until=lease_until,
        released_at=released_at,
        observed_at=observed_at,
    )


def _success_execution(
    *,
    run_id: UUID,
    task_id: str,
    dispatch_id: UUID,
) -> WorkerExecutionEvidence:
    result = SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )
    return WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=_BASE_COMMIT,
        branch_name="devflow/recovery-task",
        commit_sha="b" * 40,
        run_result=result,
        duration_ms=25,
    )


def _worker_evidence(
    execution: WorkerExecutionEvidence,
    *,
    evidence_id: int = 1,
    row_task_id: str | None = None,
    evidence_key: str | None = None,
) -> PersistedEvidence:
    return PersistedEvidence(
        id=evidence_id,
        run_id=execution.run_id,
        task_id=row_task_id or execution.task_id,
        evidence_key=evidence_key or f"dispatch:{execution.dispatch_id}:execution:{evidence_id}",
        kind=PersistenceEvidenceKind.WORKER_EXECUTION,
        stage="worker",
        schema_version=1,
        payload=execution.model_dump(mode="json"),
        payload_sha256="3" * 64,
        created_at=_NOW,
    )


def _dispatch_evidence(
    event: WorkerDispatchEvent,
    *,
    evidence_id: int = 1,
) -> PersistedEvidence:
    return PersistedEvidence(
        id=evidence_id,
        run_id=event.run_id,
        task_id=event.task_id,
        evidence_key=f"dispatch:{event.dispatch_id}:{event.phase.value.lower()}:{evidence_id}",
        kind=PersistenceEvidenceKind.DISPATCH_EVENT,
        stage="worker",
        schema_version=1,
        payload=event.model_dump(mode="json"),
        payload_sha256="4" * 64,
        created_at=_NOW,
    )


def _classify(snapshot: PersistedRunSnapshot, *leases: TaskLeaseSnapshot):
    return RecoveryStateClassifier().classify(snapshot=snapshot, leases=leases)


def test_terminal_run_is_never_reopened_by_recovery() -> None:
    snapshot = _run(status=PersistedRunStatus.SUCCEEDED)
    lease = _lease(run_id=snapshot.run_id, state=TaskLeaseState.RELEASED)

    plan = _classify(snapshot, lease)

    assert plan.tasks[0].disposition is RecoveryDisposition.NO_ACTION_RUN_TERMINAL


def test_active_owner_is_never_declared_recoverable() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="RECOVERY-1",
        dispatch_id=dispatch_id,
    )
    snapshot = _run(run_id=run_id, evidence=(_worker_evidence(execution),))
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.ACTIVE,
        dispatch_id=dispatch_id,
    )

    plan = _classify(snapshot, lease)

    assessment = plan.tasks[0]
    assert assessment.disposition is RecoveryDisposition.WAIT_ACTIVE_OWNER
    assert assessment.worker_execution_status is WorkerExecutionStatus.SUCCEEDED


def test_expired_generation_without_terminal_evidence_is_redispatch_candidate_only() -> None:
    snapshot = _run()
    lease = _lease(run_id=snapshot.run_id, state=TaskLeaseState.EXPIRED)

    plan = _classify(snapshot, lease)

    assessment = plan.tasks[0]
    assert (
        assessment.disposition
        is RecoveryDisposition.REDISPATCH_CANDIDATE_EXPIRED_GENERATION
    )
    assert assessment.worker_execution_evidence_id is None


def test_expired_generation_with_terminal_evidence_resumes_from_evidence() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="RECOVERY-1",
        dispatch_id=dispatch_id,
    )
    snapshot = _run(run_id=run_id, evidence=(_worker_evidence(execution, evidence_id=7),))
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.EXPIRED,
        dispatch_id=dispatch_id,
    )

    plan = _classify(snapshot, lease)

    assessment = plan.tasks[0]
    assert assessment.disposition is RecoveryDisposition.RESUME_FROM_TERMINAL_EVIDENCE
    assert assessment.worker_execution_evidence_id == 7
    assert assessment.worker_execution_status is WorkerExecutionStatus.SUCCEEDED


def test_released_generation_with_terminal_evidence_resumes_from_evidence() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="RECOVERY-1",
        dispatch_id=dispatch_id,
    )
    snapshot = _run(run_id=run_id, evidence=(_worker_evidence(execution),))
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.RELEASED,
        dispatch_id=dispatch_id,
    )

    plan = _classify(snapshot, lease)

    assert (
        plan.tasks[0].disposition
        is RecoveryDisposition.RESUME_FROM_TERMINAL_EVIDENCE
    )


def test_released_generation_without_terminal_evidence_stays_blocked() -> None:
    snapshot = _run()
    lease = _lease(run_id=snapshot.run_id, state=TaskLeaseState.RELEASED)

    plan = _classify(snapshot, lease)

    assert (
        plan.tasks[0].disposition
        is RecoveryDisposition.BLOCKED_RELEASED_EVIDENCE_GAP
    )


def test_unowned_task_exposes_dispatch_ambiguity_instead_of_guessing() -> None:
    snapshot = _run()
    lease = _lease(run_id=snapshot.run_id, state=TaskLeaseState.UNOWNED)

    plan = _classify(snapshot, lease)

    assert (
        plan.tasks[0].disposition
        is RecoveryDisposition.BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
    )


def test_lease_task_identity_mismatch_fails_closed() -> None:
    snapshot = _run()
    wrong = _lease(
        run_id=snapshot.run_id,
        task_id="OTHER-TASK",
        state=TaskLeaseState.UNOWNED,
    )

    with pytest.raises(PersistenceCorruptionError, match="lease/task identities disagree"):
        _classify(snapshot, wrong)


def test_worker_evidence_payload_task_identity_mismatch_fails_closed() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="OTHER-TASK",
        dispatch_id=dispatch_id,
    )
    evidence = _worker_evidence(
        execution,
        row_task_id="RECOVERY-1",
    )
    snapshot = _run(run_id=run_id, evidence=(evidence,))
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.EXPIRED,
        dispatch_id=dispatch_id,
    )

    with pytest.raises(PersistenceCorruptionError, match="payload Task identity mismatch"):
        _classify(snapshot, lease)


def test_duplicate_terminal_worker_evidence_for_one_dispatch_fails_closed() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="RECOVERY-1",
        dispatch_id=dispatch_id,
    )
    snapshot = _run(
        run_id=run_id,
        evidence=(
            _worker_evidence(execution, evidence_id=1),
            _worker_evidence(execution, evidence_id=2),
        ),
    )
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.EXPIRED,
        dispatch_id=dispatch_id,
    )

    with pytest.raises(PersistenceCorruptionError, match="multiple terminal WORKER_EXECUTION"):
        _classify(snapshot, lease)


def test_completed_dispatch_without_terminal_execution_fails_closed() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    completed = WorkerDispatchEvent(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id="RECOVERY-1",
        phase=WorkerDispatchPhase.COMPLETED,
        outcome=WorkerExecutionStatus.SUCCEEDED,
    )
    snapshot = _run(run_id=run_id, evidence=(_dispatch_evidence(completed),))
    lease = _lease(
        run_id=run_id,
        state=TaskLeaseState.EXPIRED,
        dispatch_id=dispatch_id,
    )

    with pytest.raises(PersistenceCorruptionError, match="without terminal WORKER_EXECUTION"):
        _classify(snapshot, lease)


def test_unowned_task_with_worker_side_evidence_fails_closed() -> None:
    run_id = uuid4()
    dispatch_id = uuid4()
    execution = _success_execution(
        run_id=run_id,
        task_id="RECOVERY-1",
        dispatch_id=dispatch_id,
    )
    snapshot = _run(run_id=run_id, evidence=(_worker_evidence(execution),))
    lease = _lease(run_id=run_id, state=TaskLeaseState.UNOWNED)

    with pytest.raises(PersistenceCorruptionError, match="UNOWNED task"):
        _classify(snapshot, lease)


def test_recovery_plan_serialization_never_contains_run_token() -> None:
    snapshot = _run()
    lease = _lease(run_id=snapshot.run_id, state=TaskLeaseState.EXPIRED)

    serialized = _classify(snapshot, lease).model_dump_json()

    assert "run_token" not in serialized


def test_multiple_task_leases_must_share_one_observation_time() -> None:
    snapshot = _run(task_ids=("RECOVERY-1", "RECOVERY-2"))
    first = _lease(
        run_id=snapshot.run_id,
        task_id="RECOVERY-1",
        state=TaskLeaseState.UNOWNED,
    )
    second = _lease(
        run_id=snapshot.run_id,
        task_id="RECOVERY-2",
        state=TaskLeaseState.UNOWNED,
        observed_at=_NOW + timedelta(milliseconds=1),
    )

    with pytest.raises(PersistenceCorruptionError, match="one database observation time"):
        _classify(snapshot, first, second)
