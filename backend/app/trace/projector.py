from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol
from uuid import UUID, uuid5

from pydantic import ValidationError

from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.models.events import PersistedRuntimeEvent, RuntimeEventKind
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.repair import RepairRunResult, RepairStopReason
from app.models.review import ReviewDecision, ReviewOutcome
from app.models.trace import (
    CausalRunTrace,
    CausalTraceSpan,
    TaskTraceBatch,
    TraceSpanKind,
    TraceSpanSource,
    TraceSpanStatus,
)
from app.models.verification import VerificationResult
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistenceEvidenceKind,
)

_MAX_RUNTIME_EVENT_SCAN = 5000
_EVENT_PAGE_SIZE = 500


class TraceProjectionUnavailableError(RuntimeError):
    """Raised when the bounded trace projection cannot be completed safely."""


class TraceEvidenceReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...

    async def list_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]: ...


class TraceDispatchReader(Protocol):
    async def list_for_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]: ...


class CausalTraceProjector:
    """Correlate accepted runtime facts into a diagnostic-only causal tree.

    The projector never mutates a Run and none of its output is read by scheduling, verification,
    recovery, integration or publication authority. Missing trace sidecars therefore reduce
    observability only; they never change accepted runtime truth.
    """

    def __init__(
        self,
        *,
        evidence_reader: TraceEvidenceReader,
        dispatch_reader: TraceDispatchReader,
    ) -> None:
        self._evidence_reader = evidence_reader
        self._dispatch_reader = dispatch_reader

    async def project(self, run_id: UUID) -> CausalRunTrace:
        snapshot = await self._evidence_reader.load_run(run_id)
        events = await self._load_runtime_events(run_id)
        attempts_by_task = await self._load_dispatch_attempts(snapshot)

        event_generations = self._generation_map(events)
        trace_batches = self._trace_batches(snapshot)
        batch_generations = self._batch_generation_map(trace_batches)
        generations = dict(event_generations)
        for dispatch_id, generation in batch_generations.items():
            current = generations.get(dispatch_id)
            if current is not None and current != generation:
                raise PersistenceCorruptionError(
                    "trace batch generation disagrees with runtime lease-event correlation"
                )
            generations[dispatch_id] = generation

        spans: list[CausalTraceSpan] = []
        run_span_id = uuid5(run_id, "run")
        self._append(
            spans,
            span_id=run_span_id,
            parent_span_id=None,
            run_id=run_id,
            kind=TraceSpanKind.RUN,
            status=self._run_status(snapshot.status),
            name="run",
            occurred_at=snapshot.started_at,
            source=TraceSpanSource.PERSISTED_RUN,
            source_record_id=f"run:{run_id}",
            outcome=snapshot.status.value,
        )

        task_span_ids: dict[str, UUID] = {}
        dispatch_span_ids: dict[UUID, UUID] = {}
        generation_span_ids: dict[UUID, UUID] = {}

        for persisted_task in snapshot.tasks:
            task_id = persisted_task.task.task_id
            task_span_id = uuid5(run_id, f"task:{task_id}")
            task_span_ids[task_id] = task_span_id
            self._append(
                spans,
                span_id=task_span_id,
                parent_span_id=run_span_id,
                run_id=run_id,
                task_id=task_id,
                kind=TraceSpanKind.TASK,
                status=self._task_status(snapshot, task_id),
                name=f"task.{task_id}",
                occurred_at=persisted_task.created_at,
                source=TraceSpanSource.TASK_CONTRACT,
                source_record_id=f"task:{task_id}",
            )

            for attempt in attempts_by_task[task_id]:
                if attempt.run_id != run_id or attempt.task_id != task_id:
                    raise PersistenceCorruptionError("dispatch trace identity mismatch")
                dispatch_span_id = uuid5(run_id, f"dispatch:{attempt.dispatch_id}")
                dispatch_span_ids[attempt.dispatch_id] = dispatch_span_id
                duration_ms = None
                if attempt.resolved_at is not None:
                    duration_ms = max(
                        0,
                        int((attempt.resolved_at - attempt.requested_at).total_seconds() * 1000),
                    )
                self._append(
                    spans,
                    span_id=dispatch_span_id,
                    parent_span_id=task_span_id,
                    run_id=run_id,
                    task_id=task_id,
                    dispatch_id=attempt.dispatch_id,
                    kind=TraceSpanKind.DISPATCH,
                    status=self._dispatch_status(attempt.state),
                    name=f"dispatch.attempt_{attempt.attempt_number}",
                    occurred_at=attempt.requested_at,
                    duration_ms=duration_ms,
                    source=TraceSpanSource.DISPATCH_ATTEMPT,
                    source_record_id=f"dispatch:{attempt.dispatch_id}",
                    attempt=attempt.attempt_number,
                    outcome=attempt.state.value,
                )

                generation = generations.get(attempt.dispatch_id)
                if generation is not None:
                    generation_span_id = uuid5(
                        run_id,
                        f"generation:{attempt.dispatch_id}:{generation}",
                    )
                    generation_span_ids[attempt.dispatch_id] = generation_span_id
                    correlated_events = tuple(
                        event
                        for event in events
                        if event.dispatch_id == attempt.dispatch_id
                        and event.generation == generation
                    )
                    occurred_at = (
                        min(event.created_at for event in correlated_events)
                        if correlated_events
                        else attempt.requested_at
                    )
                    released = any(
                        event.kind is RuntimeEventKind.LEASE_RELEASED for event in correlated_events
                    )
                    self._append(
                        spans,
                        span_id=generation_span_id,
                        parent_span_id=dispatch_span_id,
                        run_id=run_id,
                        task_id=task_id,
                        dispatch_id=attempt.dispatch_id,
                        generation=generation,
                        kind=TraceSpanKind.GENERATION,
                        status=TraceSpanStatus.OK if released else TraceSpanStatus.UNKNOWN,
                        name=f"worker.generation_{generation}",
                        occurred_at=occurred_at,
                        source=(
                            TraceSpanSource.RUNTIME_EVENT
                            if correlated_events
                            else TraceSpanSource.TRACE_BATCH
                        ),
                        source_record_id=(
                            f"event:{correlated_events[0].id}"
                            if correlated_events
                            else f"trace-generation:{attempt.dispatch_id}:{generation}"
                        ),
                        runtime_event_id=correlated_events[0].id if correlated_events else None,
                    )

        self._append_trace_batches(
            spans,
            trace_batches=trace_batches,
            dispatch_span_ids=dispatch_span_ids,
            generation_span_ids=generation_span_ids,
        )
        self._append_typed_execution_evidence(
            spans,
            snapshot=snapshot,
            task_span_ids=task_span_ids,
            dispatch_span_ids=dispatch_span_ids,
            generation_span_ids=generation_span_ids,
        )
        self._append_integration_evidence(
            spans,
            snapshot=snapshot,
            task_span_ids=task_span_ids,
        )

        return CausalRunTrace(
            run_id=run_id,
            root_span_id=run_span_id,
            spans=tuple(spans),
        )

    async def _load_runtime_events(self, run_id: UUID) -> tuple[PersistedRuntimeEvent, ...]:
        events: list[PersistedRuntimeEvent] = []
        cursor = 0
        while len(events) < _MAX_RUNTIME_EVENT_SCAN:
            remaining = _MAX_RUNTIME_EVENT_SCAN - len(events)
            batch = await self._evidence_reader.list_runtime_events(
                run_id,
                after_sequence=cursor,
                limit=min(_EVENT_PAGE_SIZE, remaining),
            )
            if not batch:
                return tuple(events)
            events.extend(batch)
            cursor = batch[-1].sequence
            if len(batch) < min(_EVENT_PAGE_SIZE, remaining):
                return tuple(events)

        overflow = await self._evidence_reader.list_runtime_events(
            run_id,
            after_sequence=cursor,
            limit=1,
        )
        if overflow:
            raise TraceProjectionUnavailableError(
                "Run trace exceeds the bounded runtime-event scan limit"
            )
        return tuple(events)

    async def _load_dispatch_attempts(
        self,
        snapshot: PersistedRunSnapshot,
    ) -> dict[str, tuple[PersistedDispatchAttempt, ...]]:
        task_ids = [item.task.task_id for item in snapshot.tasks]
        results = await asyncio.gather(
            *(
                self._dispatch_reader.list_for_task(
                    run_id=snapshot.run_id,
                    task_id=task_id,
                )
                for task_id in task_ids
            )
        )
        return dict(zip(task_ids, results, strict=True))

    @staticmethod
    def _generation_map(events: Sequence[PersistedRuntimeEvent]) -> dict[UUID, int]:
        generations: dict[UUID, int] = {}
        for event in events:
            if event.dispatch_id is None or event.generation is None:
                continue
            current = generations.get(event.dispatch_id)
            if current is not None and current != event.generation:
                raise PersistenceCorruptionError(
                    "one dispatch_id is correlated with multiple lease generations"
                )
            generations[event.dispatch_id] = event.generation
        return generations

    @staticmethod
    def _trace_batches(
        snapshot: PersistedRunSnapshot,
    ) -> tuple[tuple[PersistedEvidence, TaskTraceBatch], ...]:
        batches: list[tuple[PersistedEvidence, TaskTraceBatch]] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.TRACE_BATCH:
                continue
            try:
                batch = TaskTraceBatch.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"trace evidence {evidence.id} failed schema validation"
                ) from exc
            if (
                batch.run_id != snapshot.run_id
                or batch.task_id != evidence.task_id
                or batch.task_id not in {item.task.task_id for item in snapshot.tasks}
            ):
                raise PersistenceCorruptionError("trace batch identity mismatch")
            batches.append((evidence, batch))
        return tuple(batches)

    @staticmethod
    def _batch_generation_map(
        batches: Sequence[tuple[PersistedEvidence, TaskTraceBatch]],
    ) -> dict[UUID, int]:
        generations: dict[UUID, int] = {}
        for _, batch in batches:
            current = generations.get(batch.dispatch_id)
            if current is not None and current != batch.generation:
                raise PersistenceCorruptionError(
                    "one dispatch contains conflicting trace-batch generations"
                )
            generations[batch.dispatch_id] = batch.generation
        return generations

    def _append_trace_batches(
        self,
        spans: list[CausalTraceSpan],
        *,
        trace_batches: Sequence[tuple[PersistedEvidence, TaskTraceBatch]],
        dispatch_span_ids: dict[UUID, UUID],
        generation_span_ids: dict[UUID, UUID],
    ) -> None:
        existing_ids = {span.span_id for span in spans}
        for evidence, batch in trace_batches:
            dispatch_parent = generation_span_ids.get(
                batch.dispatch_id,
                dispatch_span_ids.get(batch.dispatch_id),
            )
            if dispatch_parent is None:
                raise PersistenceCorruptionError(
                    "trace batch lacks a matching durable dispatch attempt"
                )
            for item in batch.spans:
                if item.span_id in existing_ids:
                    raise PersistenceCorruptionError("causal trace span id collision")
                parent = item.parent_span_id or dispatch_parent
                if item.parent_span_id is not None and item.parent_span_id not in existing_ids:
                    raise PersistenceCorruptionError(
                        "trace batch child references a parent outside accepted projection order"
                    )
                self._append(
                    spans,
                    span_id=item.span_id,
                    parent_span_id=parent,
                    run_id=batch.run_id,
                    task_id=batch.task_id,
                    dispatch_id=batch.dispatch_id,
                    generation=batch.generation,
                    kind=item.kind,
                    status=item.status,
                    name=item.name,
                    occurred_at=evidence.created_at,
                    duration_ms=item.duration_ms,
                    source=TraceSpanSource.TRACE_BATCH,
                    source_record_id=f"evidence:{evidence.id}:span:{item.ordinal}",
                    evidence_id=evidence.id,
                    agent_role=item.agent_role,
                    model=item.model,
                    iteration=item.iteration,
                    tool_name=item.tool_name,
                    tool_error_code=item.tool_error_code,
                    attempt=item.attempt,
                    passed=item.passed,
                    outcome=item.finish_reason,
                    prompt_tokens=item.prompt_tokens,
                    completion_tokens=item.completion_tokens,
                    total_tokens=item.total_tokens,
                    enable_thinking=item.enable_thinking,
                    context_estimated_tokens=item.context_estimated_tokens,
                    estimated_prompt_tokens=item.estimated_prompt_tokens,
                    context_reused_files=item.context_reused_files,
                    context_trimmed_files=item.context_trimmed_files,
                    context_compacted_tool_groups=item.context_compacted_tool_groups,
                )
                existing_ids.add(item.span_id)

    def _append_typed_execution_evidence(
        self,
        spans: list[CausalTraceSpan],
        *,
        snapshot: PersistedRunSnapshot,
        task_span_ids: dict[str, UUID],
        dispatch_span_ids: dict[UUID, UUID],
        generation_span_ids: dict[UUID, UUID],
    ) -> None:
        for evidence in snapshot.evidence:
            if evidence.task_id is None:
                continue
            if evidence.kind in {
                PersistenceEvidenceKind.TRACE_BATCH,
                PersistenceEvidenceKind.DISPATCH_EVENT,
                PersistenceEvidenceKind.STATE_TRANSITION,
                PersistenceEvidenceKind.DEVELOPER_RUN,
                PersistenceEvidenceKind.CONTEXT_REFERENCE,
            }:
                continue
            dispatch_id = self._evidence_dispatch_id(evidence)
            parent = task_span_ids.get(evidence.task_id)
            if dispatch_id is not None:
                parent = generation_span_ids.get(
                    dispatch_id,
                    dispatch_span_ids.get(dispatch_id),
                )
            if parent is None:
                raise PersistenceCorruptionError("typed trace evidence lacks a causal parent")

            if evidence.kind is PersistenceEvidenceKind.VERIFICATION_RESULT:
                verification = self._validate(VerificationResult, evidence)
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=parent,
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    dispatch_id=dispatch_id,
                    generation=self._parent_generation(spans, parent),
                    kind=TraceSpanKind.VERIFICATION,
                    status=TraceSpanStatus.OK if verification.passed else TraceSpanStatus.ERROR,
                    name="verification.evidence",
                    occurred_at=evidence.created_at,
                    duration_ms=sum(check.duration_ms for check in verification.checks),
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    attempt=(evidence.sequence or 0) + 1,
                    passed=verification.passed,
                    outcome="PASS" if verification.passed else "FAIL",
                )
                continue

            if evidence.kind is PersistenceEvidenceKind.REVIEW_DECISION:
                decision = self._validate(ReviewDecision, evidence)
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=parent,
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    dispatch_id=dispatch_id,
                    generation=self._parent_generation(spans, parent),
                    kind=TraceSpanKind.REVIEW,
                    status=(
                        TraceSpanStatus.OK
                        if decision.decision is ReviewOutcome.PASS
                        else TraceSpanStatus.ERROR
                    ),
                    name="review.decision",
                    occurred_at=evidence.created_at,
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    attempt=(evidence.sequence or 0) + 1,
                    outcome=decision.decision.value,
                )
                continue

            if evidence.kind is PersistenceEvidenceKind.REPAIR_RUN:
                repair = self._validate(RepairRunResult, evidence)
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=parent,
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    dispatch_id=dispatch_id,
                    generation=self._parent_generation(spans, parent),
                    kind=TraceSpanKind.REPAIR,
                    status=(
                        TraceSpanStatus.OK
                        if repair.stop_reason is RepairStopReason.MODEL_STOP
                        else TraceSpanStatus.ERROR
                    ),
                    name="repair.attempt",
                    occurred_at=evidence.created_at,
                    duration_ms=repair.latency_ms,
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    attempt=repair.attempt,
                    outcome=repair.stop_reason.value,
                    prompt_tokens=repair.usage.prompt_tokens,
                    completion_tokens=repair.usage.completion_tokens,
                    total_tokens=repair.usage.total_tokens,
                )
                continue

            if evidence.kind is PersistenceEvidenceKind.WORKER_EXECUTION:
                execution = self._validate(WorkerExecutionEvidence, evidence)
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=parent,
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    dispatch_id=execution.dispatch_id,
                    generation=self._parent_generation(spans, parent),
                    kind=TraceSpanKind.WORKER_EXECUTION,
                    status=(
                        TraceSpanStatus.OK
                        if execution.status is WorkerExecutionStatus.SUCCEEDED
                        else TraceSpanStatus.ERROR
                    ),
                    name="worker.execution",
                    occurred_at=evidence.created_at,
                    duration_ms=execution.duration_ms,
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    outcome=execution.status.value,
                )
                continue

            if evidence.kind is PersistenceEvidenceKind.FAILURE_REPORT:
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=parent,
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    dispatch_id=dispatch_id,
                    generation=self._parent_generation(spans, parent),
                    kind=TraceSpanKind.FAILURE,
                    status=TraceSpanStatus.ERROR,
                    name="runtime.failure",
                    occurred_at=evidence.created_at,
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    outcome=str(evidence.payload.get("failure_type", "UNKNOWN")),
                )
                continue

            if evidence.kind is PersistenceEvidenceKind.INTEGRATION_REPAIR:
                repair = self._validate(IntegrationConflictRepairEvidence, evidence)
                self._append(
                    spans,
                    span_id=uuid5(snapshot.run_id, f"evidence:{evidence.id}"),
                    parent_span_id=task_span_ids[evidence.task_id],
                    run_id=snapshot.run_id,
                    task_id=evidence.task_id,
                    kind=TraceSpanKind.REPAIR,
                    status=TraceSpanStatus.OK,
                    name="integration.repair",
                    occurred_at=evidence.created_at,
                    source=TraceSpanSource.TYPED_EVIDENCE,
                    source_record_id=f"evidence:{evidence.id}",
                    evidence_id=evidence.id,
                    outcome="INTEGRATION_REPAIR",
                )

    def _append_integration_evidence(
        self,
        spans: list[CausalTraceSpan],
        *,
        snapshot: PersistedRunSnapshot,
        task_span_ids: dict[str, UUID],
    ) -> None:
        candidates: list[tuple[PersistedEvidence, MergeQueueSnapshot]] = []
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT:
                continue
            merge = self._validate(MergeQueueSnapshot, evidence)
            if merge.run_base_commit != snapshot.base_commit:
                raise PersistenceCorruptionError("merge trace evidence does not match Run base")
            candidates.append((evidence, merge))
        if not candidates:
            return

        evidence, merge = max(candidates, key=lambda item: item[0].id)
        for index, attempt in enumerate(merge.attempts, start=1):
            parent = task_span_ids.get(attempt.task_id)
            if parent is None:
                raise PersistenceCorruptionError("merge trace references an unknown task")
            status = (
                TraceSpanStatus.ERROR
                if attempt.outcome is MergeAttemptOutcome.CONFLICT
                else TraceSpanStatus.OK
            )
            self._append(
                spans,
                span_id=uuid5(
                    snapshot.run_id,
                    f"merge:{evidence.id}:{index}:{attempt.task_id}:{attempt.outcome.value}",
                ),
                parent_span_id=parent,
                run_id=snapshot.run_id,
                task_id=attempt.task_id,
                kind=TraceSpanKind.INTEGRATION,
                status=status,
                name="integration.attempt",
                occurred_at=evidence.created_at,
                source=TraceSpanSource.TYPED_EVIDENCE,
                source_record_id=f"evidence:{evidence.id}:merge:{index}",
                evidence_id=evidence.id,
                attempt=index,
                outcome=attempt.outcome.value,
            )

    @staticmethod
    def _evidence_dispatch_id(evidence: PersistedEvidence) -> UUID | None:
        if evidence.kind is PersistenceEvidenceKind.WORKER_EXECUTION:
            try:
                return WorkerExecutionEvidence.model_validate(evidence.payload).dispatch_id
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"worker evidence {evidence.id} failed schema validation"
                ) from exc
        prefix, separator, remainder = evidence.evidence_key.partition(":")
        if prefix != "dispatch" or not separator:
            return None
        raw_dispatch, separator, _ = remainder.partition(":")
        if not separator:
            return None
        try:
            return UUID(raw_dispatch)
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"dispatch-scoped evidence {evidence.id} has an invalid dispatch id"
            ) from exc

    @staticmethod
    def _validate(model, evidence: PersistedEvidence):
        try:
            return model.model_validate(evidence.payload)
        except ValidationError as exc:
            raise PersistenceCorruptionError(
                f"trace source evidence {evidence.id} failed {model.__name__} validation"
            ) from exc

    @staticmethod
    def _parent_generation(spans: Sequence[CausalTraceSpan], parent_span_id: UUID) -> int | None:
        for span in reversed(spans):
            if span.span_id == parent_span_id:
                return span.generation
        return None

    @staticmethod
    def _run_status(status: PersistedRunStatus) -> TraceSpanStatus:
        if status is PersistedRunStatus.SUCCEEDED:
            return TraceSpanStatus.OK
        if status is PersistedRunStatus.FAILED:
            return TraceSpanStatus.ERROR
        return TraceSpanStatus.UNKNOWN

    @staticmethod
    def _dispatch_status(state: DispatchAttemptState) -> TraceSpanStatus:
        if state is DispatchAttemptState.ENQUEUED:
            return TraceSpanStatus.OK
        if state is DispatchAttemptState.PUBLISH_FAILED:
            return TraceSpanStatus.ERROR
        return TraceSpanStatus.UNKNOWN

    @staticmethod
    def _task_status(snapshot: PersistedRunSnapshot, task_id: str) -> TraceSpanStatus:
        status = TraceSpanStatus.UNKNOWN
        for evidence in snapshot.evidence:
            if (
                evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION
                or evidence.task_id != task_id
            ):
                continue
            try:
                execution = WorkerExecutionEvidence.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"worker evidence {evidence.id} failed schema validation"
                ) from exc
            status = (
                TraceSpanStatus.OK
                if execution.status is WorkerExecutionStatus.SUCCEEDED
                else TraceSpanStatus.ERROR
            )
        return status

    @staticmethod
    def _append(
        spans: list[CausalTraceSpan],
        *,
        span_id: UUID,
        parent_span_id: UUID | None,
        run_id: UUID,
        kind: TraceSpanKind,
        status: TraceSpanStatus,
        name: str,
        occurred_at,
        source: TraceSpanSource,
        source_record_id: str,
        task_id: str | None = None,
        dispatch_id: UUID | None = None,
        generation: int | None = None,
        duration_ms: int | None = None,
        evidence_id: int | None = None,
        runtime_event_id: int | None = None,
        agent_role=None,
        model: str | None = None,
        iteration: int | None = None,
        tool_name: str | None = None,
        tool_error_code=None,
        attempt: int | None = None,
        passed: bool | None = None,
        outcome: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        context_estimated_tokens: int = 0,
        estimated_prompt_tokens: int = 0,
        context_reused_files: int = 0,
        context_trimmed_files: int = 0,
        context_compacted_tool_groups: int = 0,
        enable_thinking: bool = False,
    ) -> None:
        spans.append(
            CausalTraceSpan(
                span_id=span_id,
                parent_span_id=parent_span_id,
                run_id=run_id,
                task_id=task_id,
                dispatch_id=dispatch_id,
                generation=generation,
                kind=kind,
                status=status,
                name=name,
                sequence=len(spans) + 1,
                occurred_at=occurred_at,
                duration_ms=duration_ms,
                source=source,
                source_record_id=source_record_id,
                evidence_id=evidence_id,
                runtime_event_id=runtime_event_id,
                agent_role=agent_role,
                model=model,
                iteration=iteration,
                tool_name=tool_name,
                tool_error_code=tool_error_code,
                attempt=attempt,
                passed=passed,
                outcome=outcome,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                context_estimated_tokens=context_estimated_tokens,
                estimated_prompt_tokens=estimated_prompt_tokens,
                context_reused_files=context_reused_files,
                context_trimmed_files=context_trimmed_files,
                context_compacted_tool_groups=context_compacted_tool_groups,
                enable_thinking=enable_thinking,
            )
        )
