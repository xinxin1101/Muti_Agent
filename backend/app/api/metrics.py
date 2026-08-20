from __future__ import annotations

from collections import Counter
from datetime import timedelta
from uuid import UUID

from app.api.models import (
    ProductEvidenceMetrics,
    ProductRunMetrics,
    ProductRuntimeEventMetrics,
)
from app.models.developer import DeveloperRunResult
from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventAggregate,
    RuntimeEventKind,
    RuntimeEventLevel,
)
from app.models.failure import FailureReport, FailureType
from app.models.repair import RepairRunResult
from app.models.review import ReviewDecision, ReviewOutcome
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
        raise PersistenceCorruptionError(
            f"{label} token usage is internally inconsistent"
        )


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


def build_run_metrics(
    snapshot: PersistedRunSnapshot,
    runtime_events: RuntimeEventAggregate,
) -> ProductRunMetrics:
    """Summarize accepted facts without deriving or authorizing Run success."""

    counts = Counter(item.kind for item in snapshot.evidence)
    typed = _typed_evidence_metrics(snapshot)
    terminal_duration_ms: int | None = None
    if snapshot.finished_at is not None:
        elapsed = snapshot.finished_at - snapshot.started_at
        if elapsed < timedelta(0):
            raise PersistenceCorruptionError(
                "persisted Run finished_at cannot precede started_at"
            )
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
    )
