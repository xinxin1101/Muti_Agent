from __future__ import annotations

from collections import Counter
from datetime import timedelta
from uuid import UUID

from app.api.models import (
    ProductEvidenceMetrics,
    ProductPlanningTokenBudget,
    ProductRoleTokenUsage,
    ProductRunMetrics,
    ProductRuntimeEventMetrics,
    ProductRunTokenBudget,
    ProductStagePerformanceMetrics,
    ProductStageTokenBudget,
    ProductWorkflowMetrics,
    ProductWorkPackageTokenBudget,
)
from app.models.agent import AgentRole
from app.models.developer import DeveloperRunResult
from app.models.dispatch import WorkerExecutionEvidence
from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventAggregate,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from app.models.failure import FailureReport, FailureType
from app.models.repair import RepairRunResult
from app.models.review import ReviewDecision, ReviewOutcome
from app.models.token_budget import PlanningTokenBudget, RunTokenBudget
from app.models.trace import TaskTraceBatch, TraceSpanKind
from app.models.workflow import (
    WorkflowActivationMode,
    WorkflowExecutionMode,
    WorkflowExecutionRecord,
)
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.serialization import decode_evidence
from app.persistence.types import PersistedRunSnapshot, PersistenceEvidenceKind

METRIC_EVENT_PAGE_SIZE = 1000
MAX_METRIC_EVENT_SCAN = 10_000


def aggregate_runtime_events(
    run_id: UUID,
    events: tuple[PersistedRuntimeEvent, ...],
) -> RuntimeEventAggregate:
    """Validate ordered Run event history and reduce it to bounded observability counters."""

    warning_events = 0
    error_events = 0
    lease_acquisitions = 0
    lease_takeovers = 0
    lease_releases = 0
    expected_sequence = 1

    for event in events:
        if event.run_id != run_id:
            raise PersistenceCorruptionError(
                "runtime event metrics encountered an event from a different Run"
            )
        if event.sequence != expected_sequence:
            raise PersistenceCorruptionError(
                "runtime event metrics require a contiguous Run sequence starting at one"
            )
        expected_sequence += 1

        if event.level is RuntimeEventLevel.WARNING:
            warning_events += 1
        elif event.level is RuntimeEventLevel.ERROR:
            error_events += 1

        if event.kind is RuntimeEventKind.LEASE_ACQUIRED:
            lease_acquisitions += 1
        elif event.kind is RuntimeEventKind.LEASE_TAKEN_OVER:
            lease_takeovers += 1
        elif event.kind is RuntimeEventKind.LEASE_RELEASED:
            lease_releases += 1

    try:
        return RuntimeEventAggregate(
            total_events=len(events),
            warning_events=warning_events,
            error_events=error_events,
            lease_acquisitions=lease_acquisitions,
            lease_takeovers=lease_takeovers,
            lease_releases=lease_releases,
            latest_sequence=events[-1].sequence if events else 0,
        )
    except ValueError as exc:
        raise PersistenceCorruptionError(
            "runtime event metrics failed aggregate integrity validation"
        ) from exc


def _validate_token_usage(prompt: int, completion: int, total: int, *, label: str) -> None:
    if prompt + completion != total:
        raise PersistenceCorruptionError(f"{label} token usage is internally inconsistent")


def _typed_evidence_metrics(snapshot: PersistedRunSnapshot) -> dict[str, int]:
    reviewer_rejections = 0
    scope_violations = 0
    developer_prompt_tokens = 0
    developer_completion_tokens = 0
    developer_total_tokens = 0
    repair_prompt_tokens = 0
    repair_completion_tokens = 0
    repair_total_tokens = 0

    for item in snapshot.evidence:
        if item.kind not in {
            PersistenceEvidenceKind.DEVELOPER_RUN,
            PersistenceEvidenceKind.REVIEW_DECISION,
            PersistenceEvidenceKind.REPAIR_RUN,
            PersistenceEvidenceKind.FAILURE_REPORT,
        }:
            continue

        decoded = decode_evidence(item.kind, item.payload)
        if isinstance(decoded, DeveloperRunResult):
            usage = decoded.usage
            _validate_token_usage(
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                label="Developer",
            )
            developer_prompt_tokens += usage.prompt_tokens
            developer_completion_tokens += usage.completion_tokens
            developer_total_tokens += usage.total_tokens
        elif isinstance(decoded, RepairRunResult):
            usage = decoded.usage
            _validate_token_usage(
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                label="Repair",
            )
            repair_prompt_tokens += usage.prompt_tokens
            repair_completion_tokens += usage.completion_tokens
            repair_total_tokens += usage.total_tokens
        elif isinstance(decoded, ReviewDecision):
            if decoded.decision is ReviewOutcome.CHANGES_REQUESTED:
                reviewer_rejections += 1
        elif isinstance(decoded, FailureReport):
            if decoded.failure_type is FailureType.SCOPE_VIOLATION:
                scope_violations += 1

    return {
        "reviewer_rejections": reviewer_rejections,
        "scope_violations": scope_violations,
        "developer_prompt_tokens": developer_prompt_tokens,
        "developer_completion_tokens": developer_completion_tokens,
        "developer_total_tokens": developer_total_tokens,
        "repair_prompt_tokens": repair_prompt_tokens,
        "repair_completion_tokens": repair_completion_tokens,
        "repair_total_tokens": repair_total_tokens,
    }


def _stage_performance_metrics(snapshot: PersistedRunSnapshot) -> ProductStagePerformanceMetrics:
    """Reduce persisted trace sidecars without making them execution authority."""

    developer_model_latency_ms = 0
    repair_model_latency_ms = 0
    repository_tool_latency_ms = 0
    verification_latency_ms = 0
    context_estimated_tokens = 0
    estimated_prompt_tokens = 0
    actual_prompt_tokens = 0
    context_reused_files = 0
    context_trimmed_files = 0
    context_compacted_tool_groups = 0

    for item in snapshot.evidence:
        if item.kind is PersistenceEvidenceKind.TRACE_BATCH:
            decoded = decode_evidence(item.kind, item.payload)
            if not isinstance(decoded, TaskTraceBatch):
                raise PersistenceCorruptionError("TRACE_BATCH decoded to an unexpected model")
            for span in decoded.spans:
                if span.kind is TraceSpanKind.AGENT_TURN:
                    context_estimated_tokens += span.context_estimated_tokens
                    estimated_prompt_tokens += span.estimated_prompt_tokens
                    actual_prompt_tokens += span.prompt_tokens
                    context_reused_files += span.context_reused_files
                    context_trimmed_files += span.context_trimmed_files
                    context_compacted_tool_groups += span.context_compacted_tool_groups
                    if span.agent_role is AgentRole.DEVELOPER:
                        developer_model_latency_ms += span.duration_ms
                    elif span.agent_role is AgentRole.REPAIR:
                        repair_model_latency_ms += span.duration_ms
                elif span.kind is TraceSpanKind.TOOL_CALL:
                    repository_tool_latency_ms += span.duration_ms
                elif span.kind is TraceSpanKind.VERIFICATION:
                    verification_latency_ms += span.duration_ms

    return ProductStagePerformanceMetrics(
        developer_model_latency_ms=developer_model_latency_ms,
        repair_model_latency_ms=repair_model_latency_ms,
        repository_tool_latency_ms=repository_tool_latency_ms,
        verification_latency_ms=verification_latency_ms,
        context_estimated_tokens=context_estimated_tokens,
        estimated_prompt_tokens=estimated_prompt_tokens,
        actual_prompt_tokens=actual_prompt_tokens,
        prompt_estimate_error_ratio=(
            abs(estimated_prompt_tokens - actual_prompt_tokens) / max(1, actual_prompt_tokens)
        ),
        context_reused_files=context_reused_files,
        context_trimmed_files=context_trimmed_files,
        context_compacted_tool_groups=context_compacted_tool_groups,
    )


def _workflow_metrics(
    snapshot: PersistedRunSnapshot,
    token_budget: RunTokenBudget,
    *,
    activation_mode: WorkflowActivationMode,
) -> ProductWorkflowMetrics:
    """Reduce persisted execution routes; estimates never replace actual token accounting."""

    latest_records: dict[str, WorkflowExecutionRecord] = {}
    for item in snapshot.evidence:
        if item.kind is PersistenceEvidenceKind.WORKFLOW_EXECUTION:
            decoded = decode_evidence(item.kind, item.payload)
            if not isinstance(decoded, WorkflowExecutionRecord):
                raise PersistenceCorruptionError(
                    "WORKFLOW_EXECUTION decoded to an unexpected model"
                )
            latest_records[decoded.task_id] = decoded
    # Historical metrics fixtures and legacy Runs can contain partial Worker evidence. Only
    # decode it when it contributes duration to a persisted Workflow route; the execution record
    # itself remains the source of route truth.
    worker_durations: dict[str, int] = {}
    for item in snapshot.evidence:
        if item.kind is PersistenceEvidenceKind.WORKER_EXECUTION and item.task_id in latest_records:
            decoded = decode_evidence(item.kind, item.payload)
            if not isinstance(decoded, WorkerExecutionEvidence):
                raise PersistenceCorruptionError("WORKER_EXECUTION decoded to an unexpected model")
            if decoded.run_result is not None and decoded.run_result.workflow_execution is not None:
                worker_durations[decoded.task_id] = decoded.duration_ms

    counts = Counter(record.mode for record in latest_records.values())
    agent_calls = sum(
        role.call_count
        for role in token_budget.roles
        if role.role in {AgentRole.DEVELOPER, AgentRole.REPAIR, AgentRole.REVIEWER}
    )
    workflow_records = [
        record
        for record in latest_records.values()
        if record.mode is WorkflowExecutionMode.WORKFLOW
    ]
    return ProductWorkflowMetrics(
        activation_mode=activation_mode,
        workflow_tasks=counts[WorkflowExecutionMode.WORKFLOW],
        agent_tasks=counts[WorkflowExecutionMode.AGENT],
        hybrid_tasks=counts[WorkflowExecutionMode.HYBRID],
        workflow_calls=sum(max(1, record.attempts) for record in workflow_records),
        agent_calls=agent_calls,
        workflow_duration_ms=sum(
            worker_durations.get(record.task_id, 0) for record in workflow_records
        ),
        estimated_tokens_saved=sum(record.estimated_tokens_saved for record in workflow_records),
        agent_escalations=sum(
            record.mode is WorkflowExecutionMode.HYBRID
            and bool(record.fallback_reason)
            and "escalated" in record.fallback_reason.lower()
            for record in latest_records.values()
        ),
    )


def build_run_metrics(
    snapshot: PersistedRunSnapshot,
    runtime_events: RuntimeEventAggregate,
    token_budget: RunTokenBudget | None = None,
    planning_budget: PlanningTokenBudget | None = None,
    *,
    workflow_activation_mode: WorkflowActivationMode = WorkflowActivationMode.WORKFLOW_FIRST,
) -> ProductRunMetrics:
    """Summarize accepted facts without deriving or authorizing Run success."""

    token_budget = token_budget or RunTokenBudget(total_budget_tokens=30_000)
    counts = Counter(item.kind for item in snapshot.evidence)
    typed = _typed_evidence_metrics(snapshot)
    terminal_duration_ms: int | None = None
    if snapshot.finished_at is not None:
        elapsed = snapshot.finished_at - snapshot.started_at
        if elapsed < timedelta(0):
            raise PersistenceCorruptionError("persisted Run finished_at cannot precede started_at")
        terminal_duration_ms = int(elapsed / timedelta(milliseconds=1))

    return ProductRunMetrics(
        run_id=snapshot.run_id,
        project_id=snapshot.project_id,
        status=snapshot.status,
        task_count=len(snapshot.tasks),
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        terminal_duration_ms=terminal_duration_ms,
        evidence=ProductEvidenceMetrics(
            total_records=len(snapshot.evidence),
            developer_runs=counts[PersistenceEvidenceKind.DEVELOPER_RUN],
            verification_attempts=counts[PersistenceEvidenceKind.VERIFICATION_RESULT],
            review_decisions=counts[PersistenceEvidenceKind.REVIEW_DECISION],
            reviewer_rejections=typed["reviewer_rejections"],
            repair_attempts=counts[PersistenceEvidenceKind.REPAIR_RUN],
            failure_reports=counts[PersistenceEvidenceKind.FAILURE_REPORT],
            scope_violations=typed["scope_violations"],
            dispatch_events=counts[PersistenceEvidenceKind.DISPATCH_EVENT],
            worker_executions=counts[PersistenceEvidenceKind.WORKER_EXECUTION],
            merge_queue_snapshots=counts[PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT],
            merge_conflicts=counts[PersistenceEvidenceKind.MERGE_CONFLICT],
            integration_gate_evaluations=counts[PersistenceEvidenceKind.INTEGRATION_GATE],
            human_decisions=counts[PersistenceEvidenceKind.HUMAN_DECISION],
            developer_prompt_tokens=typed["developer_prompt_tokens"],
            developer_completion_tokens=typed["developer_completion_tokens"],
            developer_total_tokens=typed["developer_total_tokens"],
            repair_prompt_tokens=typed["repair_prompt_tokens"],
            repair_completion_tokens=typed["repair_completion_tokens"],
            repair_total_tokens=typed["repair_total_tokens"],
            reviewer_token_usage_available=False,
            estimated_cost_available=False,
        ),
        runtime_events=ProductRuntimeEventMetrics(
            total_events=runtime_events.total_events,
            warning_events=runtime_events.warning_events,
            error_events=runtime_events.error_events,
            lease_acquisitions=runtime_events.lease_acquisitions,
            lease_takeovers=runtime_events.lease_takeovers,
            lease_releases=runtime_events.lease_releases,
            latest_sequence=runtime_events.latest_sequence,
        ),
        token_budget=ProductRunTokenBudget(
            total_budget_tokens=token_budget.total_budget_tokens,
            used_prompt_tokens=token_budget.used_prompt_tokens,
            used_completion_tokens=token_budget.used_completion_tokens,
            used_total_tokens=token_budget.used_total_tokens,
            reserved_tokens=token_budget.reserved_tokens,
            status=token_budget.status,
            roles=tuple(
                ProductRoleTokenUsage(
                    role=item.role.value,
                    prompt_tokens=item.prompt_tokens,
                    completion_tokens=item.completion_tokens,
                    total_tokens=item.total_tokens,
                    call_count=item.call_count,
                )
                for item in token_budget.roles
            ),
            stages=tuple(
                ProductStageTokenBudget(
                    stage=item.stage.value,
                    total_budget_tokens=item.total_budget_tokens,
                    used_tokens=item.used_tokens,
                    reserved_tokens=item.reserved_tokens,
                )
                for item in token_budget.stages
            ),
            work_packages=tuple(
                ProductWorkPackageTokenBudget(
                    task_id=item.task_id,
                    complexity=item.complexity,
                    total_budget_tokens=item.total_budget_tokens,
                    developer_budget_tokens=item.developer_budget_tokens,
                    repair_budget_tokens=item.repair_budget_tokens,
                    developer_used_tokens=item.developer_used_tokens,
                    repair_used_tokens=item.repair_used_tokens,
                    developer_reserved_tokens=item.developer_reserved_tokens,
                    repair_reserved_tokens=item.repair_reserved_tokens,
                    developer_borrowed_tokens=item.developer_borrowed_tokens,
                    repair_borrowed_tokens=item.repair_borrowed_tokens,
                    developer_reclaimed_tokens=item.developer_reclaimed_tokens,
                    repair_reclaimed_tokens=item.repair_reclaimed_tokens,
                    borrow_count=item.borrow_count,
                    last_required_tokens=item.last_required_tokens,
                    last_available_tokens=item.last_available_tokens,
                    last_flex_available_tokens=item.last_flex_available_tokens,
                    last_borrowed_tokens=item.last_borrowed_tokens,
                    last_budget_decision=item.last_budget_decision,
                    status=item.status.value,
                )
                for item in token_budget.work_packages
            ),
        ),
        planning_budget=(
            ProductPlanningTokenBudget(
                total_budget_tokens=planning_budget.total_budget_tokens,
                used_total_tokens=planning_budget.used_total_tokens,
                attempt_count=planning_budget.attempt_count,
                max_attempts=planning_budget.max_attempts,
                enable_thinking=planning_budget.enable_thinking,
                status=planning_budget.status,
            )
            if planning_budget is not None
            else None
        ),
        performance=_stage_performance_metrics(snapshot),
        workflow=_workflow_metrics(
            snapshot,
            token_budget,
            activation_mode=workflow_activation_mode,
        ),
    )
