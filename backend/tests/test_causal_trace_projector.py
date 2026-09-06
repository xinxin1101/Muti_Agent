import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.models.agent import AgentResponse, AgentRole, TokenUsage
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.review import ReviewDecision, ReviewOutcome
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.models.tools import ToolCall, ToolExecutionResult
from app.models.trace import TraceSpanKind, TraceSpanSource
from app.models.verification import CheckResult, CheckType, VerificationResult
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.trace.collector import TaskTraceCollector
from app.trace.projector import CausalTraceProjector


class _EvidenceReader:
    def __init__(
        self,
        snapshot: PersistedRunSnapshot,
        events: tuple[PersistedRuntimeEvent, ...],
    ) -> None:
        self.snapshot = snapshot
        self.events = events

    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot:
        if run_id != self.snapshot.run_id:
            raise ValueError("unknown run")
        return self.snapshot

    async def list_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]:
        if run_id != self.snapshot.run_id:
            raise ValueError("unknown run")
        return tuple(event for event in self.events if event.sequence > after_sequence)[:limit]


class _DispatchReader:
    def __init__(self, attempt: PersistedDispatchAttempt) -> None:
        self.attempt = attempt

    async def list_for_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]:
        if run_id != self.attempt.run_id or task_id != self.attempt.task_id:
            return ()
        return (self.attempt,)


def _task() -> TaskContract:
    return TaskContract(
        task_id="task-a",
        objective="Implement the traced feature.",
        writable_files=["feature.py"],
        acceptance_criteria=["feature works"],
        verification_commands=["pytest -q"],
    )


def _verification() -> VerificationResult:
    return VerificationResult(
        passed=True,
        checks=[
            CheckResult(
                check_type=CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=True,
                exit_code=0,
                duration_ms=12,
            )
        ],
    )


def _runtime_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="created"),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="accepted"),
        ],
        changed_files=["feature.py"],
    )


def _evidence(
    *,
    evidence_id: int,
    run_id: UUID,
    task_id: str | None,
    key: str,
    kind: PersistenceEvidenceKind,
    payload: dict,
    created_at: datetime,
    stage: str | None = None,
    sequence: int | None = None,
) -> PersistedEvidence:
    return PersistedEvidence(
        id=evidence_id,
        run_id=run_id,
        task_id=task_id,
        evidence_key=key,
        kind=kind,
        stage=stage,
        sequence=sequence,
        schema_version=1,
        payload=payload,
        payload_sha256="0" * 64,
        created_at=created_at,
    )


def _fixture(*, batch_generation: int = 2):
    now = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    run_id = uuid4()
    dispatch_id = uuid4()
    base_commit = "a" * 40
    task_commit = "b" * 40
    integration_commit = "c" * 40
    task = _task()

    collector = TaskTraceCollector(
        run_id=run_id,
        task_id=task.task_id,
        dispatch_id=dispatch_id,
        generation=batch_generation,
    )
    turn_id = collector.record_agent_turn(
        role=AgentRole.DEVELOPER,
        iteration=1,
        response=AgentResponse(
            model="dev-model",
            content="secret completion that is not persisted in trace",
            tool_calls=[ToolCall(id="call-1", name="read_file", arguments="{}")],
            usage=TokenUsage(prompt_tokens=4, completion_tokens=3, total_tokens=7),
            latency_ms=8,
            finish_reason="tool_calls",
        ),
    )
    collector.record_tool_call(
        role=AgentRole.DEVELOPER,
        iteration=1,
        parent_span_id=turn_id,
        result=ToolExecutionResult(
            tool_call_id="call-1",
            name="read_file",
            ok=True,
            content="private repository content",
        ),
        duration_ms=2,
    )
    collector.record_runtime_progress(
        agent_turn_span_id=turn_id,
        has_workspace_patch=True,
        turn_made_progress=True,
        changed_files_this_turn=("feature.py",),
        consecutive_mutation_turns=1,
        same_file_mutation_streak=1,
        convergence_nudge_triggered=True,
    )
    collector.record_verification(
        attempt=1,
        result=_verification(),
        duration_ms=13,
    )

    worker = WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task.task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base_commit,
        branch_name="devflow/task-a",
        commit_sha=task_commit,
        run_result=_runtime_result(task.task_id),
        duration_ms=50,
    )
    merge = MergeQueueSnapshot(
        integration_ref=f"refs/devflow/integration/run-{run_id.hex}",
        run_base_commit=base_commit,
        head_commit=integration_commit,
        integrated_task_ids=(task.task_id,),
        attempts=(
            MergeQueueAttempt(
                sequence=0,
                task_id=task.task_id,
                task_branch="devflow/task-a",
                task_base_commit=base_commit,
                task_commit=task_commit,
                previous_integration_commit=base_commit,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=integration_commit,
            ),
        ),
    )
    review = ReviewDecision(
        decision=ReviewOutcome.PASS,
        summary="Implementation satisfies the task.",
    )
    verification = _verification()

    evidence = (
        _evidence(
            evidence_id=1,
            run_id=run_id,
            task_id=task.task_id,
            key=f"dispatch:{dispatch_id}:trace",
            kind=PersistenceEvidenceKind.TRACE_BATCH,
            payload=collector.batch().model_dump(mode="json"),
            created_at=now + timedelta(seconds=2),
            stage="trace",
        ),
        _evidence(
            evidence_id=2,
            run_id=run_id,
            task_id=task.task_id,
            key=f"dispatch:{dispatch_id}:verification:0000",
            kind=PersistenceEvidenceKind.VERIFICATION_RESULT,
            payload=verification.model_dump(mode="json"),
            created_at=now + timedelta(seconds=3),
            stage="verification",
            sequence=0,
        ),
        _evidence(
            evidence_id=3,
            run_id=run_id,
            task_id=task.task_id,
            key=f"dispatch:{dispatch_id}:review:0000",
            kind=PersistenceEvidenceKind.REVIEW_DECISION,
            payload=review.model_dump(mode="json"),
            created_at=now + timedelta(seconds=4),
            stage="review",
            sequence=0,
        ),
        _evidence(
            evidence_id=4,
            run_id=run_id,
            task_id=task.task_id,
            key=f"dispatch:{dispatch_id}:execution",
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            payload=worker.model_dump(mode="json"),
            created_at=now + timedelta(seconds=5),
            stage="worker",
        ),
        _evidence(
            evidence_id=5,
            run_id=run_id,
            task_id=None,
            key="integration:merge-queue:complete",
            kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
            payload=merge.model_dump(mode="json"),
            created_at=now + timedelta(seconds=6),
            stage="integration",
        ),
    )
    snapshot = PersistedRunSnapshot(
        run_id=run_id,
        project_id=uuid4(),
        repository_url="https://github.com/example/project.git",
        default_branch="main",
        base_commit=base_commit,
        status=PersistedRunStatus.RUNNING,
        tasks=(
            PersistedTask(
                task=task,
                contract_sha256="1" * 64,
                created_at=now,
            ),
        ),
        evidence=evidence,
        started_at=now,
    )
    attempt = PersistedDispatchAttempt(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task.task_id,
        attempt_number=1,
        state=DispatchAttemptState.ENQUEUED,
        broker_message_id="message-1",
        queue_name="devflow_tasks",
        requested_at=now + timedelta(seconds=1),
        resolved_at=now + timedelta(seconds=1, milliseconds=20),
        updated_at=now + timedelta(seconds=1, milliseconds=20),
    )
    events = (
        PersistedRuntimeEvent(
            id=1,
            event_id=uuid4(),
            run_id=run_id,
            sequence=1,
            event_key="lease:acquired",
            kind=RuntimeEventKind.LEASE_ACQUIRED,
            source=RuntimeEventSource.LEASE,
            level=RuntimeEventLevel.INFO,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
            generation=2,
            message="lease acquired",
            schema_version=1,
            attributes={},
            attributes_sha256="2" * 64,
            created_at=now + timedelta(seconds=1, milliseconds=30),
        ),
        PersistedRuntimeEvent(
            id=2,
            event_id=uuid4(),
            run_id=run_id,
            sequence=2,
            event_key="lease:released",
            kind=RuntimeEventKind.LEASE_RELEASED,
            source=RuntimeEventSource.LEASE,
            level=RuntimeEventLevel.INFO,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
            generation=2,
            message="lease released",
            schema_version=1,
            attributes={},
            attributes_sha256="3" * 64,
            created_at=now + timedelta(seconds=5, milliseconds=10),
        ),
    )
    return snapshot, attempt, events


def test_projector_builds_full_diagnostic_causal_tree() -> None:
    asyncio.run(_projector_builds_full_diagnostic_causal_tree())


async def _projector_builds_full_diagnostic_causal_tree() -> None:
    snapshot, attempt, events = _fixture()
    trace = await CausalTraceProjector(
        evidence_reader=_EvidenceReader(snapshot, events),
        dispatch_reader=_DispatchReader(attempt),
    ).project(snapshot.run_id)

    assert trace.diagnostic_only is True
    assert trace.privacy_mode == "METADATA_ONLY"
    assert trace.spans[0].kind is TraceSpanKind.RUN

    by_kind = {kind: [span for span in trace.spans if span.kind is kind] for kind in TraceSpanKind}
    assert len(by_kind[TraceSpanKind.TASK]) == 1
    assert len(by_kind[TraceSpanKind.DISPATCH]) == 1
    assert len(by_kind[TraceSpanKind.GENERATION]) == 1
    assert by_kind[TraceSpanKind.GENERATION][0].generation == 2
    assert len(by_kind[TraceSpanKind.AGENT_TURN]) == 1
    assert len(by_kind[TraceSpanKind.TOOL_CALL]) == 1
    assert len(by_kind[TraceSpanKind.VERIFICATION]) >= 1
    assert len(by_kind[TraceSpanKind.REVIEW]) == 1
    assert len(by_kind[TraceSpanKind.WORKER_EXECUTION]) == 1
    assert len(by_kind[TraceSpanKind.INTEGRATION]) == 1

    generation = by_kind[TraceSpanKind.GENERATION][0]
    turn = by_kind[TraceSpanKind.AGENT_TURN][0]
    tool = by_kind[TraceSpanKind.TOOL_CALL][0]
    assert turn.parent_span_id == generation.span_id
    assert tool.parent_span_id == turn.span_id
    assert turn.source is TraceSpanSource.TRACE_BATCH
    assert turn.prompt_tokens == 4
    assert turn.completion_tokens == 3
    assert turn.total_tokens == 7
    assert turn.has_workspace_patch is True
    assert turn.turn_made_progress is True
    assert turn.changed_files_this_turn == ("feature.py",)
    assert turn.consecutive_mutation_turns == 1
    assert turn.same_file_mutation_streak == 1
    assert turn.convergence_nudge_triggered is True

    serialized = trace.model_dump_json()
    assert "secret completion" not in serialized
    assert "private repository content" not in serialized


def test_projector_rejects_generation_mismatch() -> None:
    asyncio.run(_projector_rejects_generation_mismatch())


async def _projector_rejects_generation_mismatch() -> None:
    snapshot, attempt, events = _fixture(batch_generation=1)
    projector = CausalTraceProjector(
        evidence_reader=_EvidenceReader(snapshot, events),
        dispatch_reader=_DispatchReader(attempt),
    )

    with pytest.raises(PersistenceCorruptionError, match="generation disagrees"):
        await projector.project(snapshot.run_id)
