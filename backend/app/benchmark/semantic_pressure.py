from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from app.benchmark.convergence import (
    ConvergenceExpectationKind,
    ConvergenceIssueExpectation,
)
from app.benchmark.models import BenchmarkModel
from app.models.review import ReviewDecision, ReviewIssue, ReviewOutcome


class SemanticPressureWorkload(StrEnum):
    PYTHON_BACKEND_CORRECTNESS = "PYTHON_BACKEND_CORRECTNESS"
    API_CONTRACT = "API_CONTRACT"
    DATABASE_TRANSACTION = "DATABASE_TRANSACTION"
    CONCURRENCY = "CONCURRENCY"
    SECURITY_BOUNDARY = "SECURITY_BOUNDARY"
    MULTI_FILE_REFACTOR = "MULTI_FILE_REFACTOR"
    CONFIG_DEPLOYMENT = "CONFIG_DEPLOYMENT"
    FRONTEND_STATE = "FRONTEND_STATE"
    CROSS_FILE_COMPATIBILITY = "CROSS_FILE_COMPATIBILITY"
    REPAIR_INDUCED_REGRESSION = "REPAIR_INDUCED_REGRESSION"


class SemanticPressureRepairScope(StrEnum):
    LOCAL = "LOCAL"
    CONTRACT_TOUCHING = "CONTRACT_TOUCHING"
    STATEFUL = "STATEFUL"
    SECURITY_TOUCHING = "SECURITY_TOUCHING"
    CROSS_FILE = "CROSS_FILE"
    CONFIGURATION = "CONFIGURATION"
    FRONTEND_STATE = "FRONTEND_STATE"


class SemanticPressureIssueOrigin(StrEnum):
    PREEXISTING = "PREEXISTING"
    REPAIR_INDUCED = "REPAIR_INDUCED"


class SemanticPressureIssueClass(StrEnum):
    INITIAL_PRIMARY = "INITIAL_PRIMARY"
    INITIAL_VALID_BLOCKER = "INITIAL_VALID_BLOCKER"
    INITIAL_CHURN_CANDIDATE = "INITIAL_CHURN_CANDIDATE"
    INITIAL_UNMATCHED = "INITIAL_UNMATCHED"
    RECURRING_PRIMARY = "RECURRING_PRIMARY"
    LEGITIMATE_NEW_BLOCKER = "LEGITIMATE_NEW_BLOCKER"
    CONFIRMED_BASELINE_CHURN = "CONFIRMED_BASELINE_CHURN"
    REPAIR_INDUCED_ISSUE = "REPAIR_INDUCED_ISSUE"
    UNREGISTERED_NEW_ISSUE = "UNREGISTERED_NEW_ISSUE"


class SemanticPressureVerdict(StrEnum):
    BASELINE_CHURN_CONFIRMED = "BASELINE_CHURN_CONFIRMED"
    NO_BASELINE_CHURN = "NO_BASELINE_CHURN"
    PRIMARY_UNRESOLVED = "PRIMARY_UNRESOLVED"
    LEGITIMATE_NEW_BLOCKER = "LEGITIMATE_NEW_BLOCKER"
    REPAIR_INDUCED_REGRESSION = "REPAIR_INDUCED_REGRESSION"
    VERIFICATION_INTERCEPTED = "VERIFICATION_INTERCEPTED"
    INCONCLUSIVE_PRIMARY_MISSED = "INCONCLUSIVE_PRIMARY_MISSED"
    INCONCLUSIVE_INITIAL_CHURN_SEEN = "INCONCLUSIVE_INITIAL_CHURN_SEEN"
    INCONCLUSIVE_UNREGISTERED_ISSUE = "INCONCLUSIVE_UNREGISTERED_ISSUE"
    INCONCLUSIVE_NO_FOLLOWUP_REVIEW = "INCONCLUSIVE_NO_FOLLOWUP_REVIEW"


class SemanticPressureExpectation(BenchmarkModel):
    matcher: ConvergenceIssueExpectation
    origin: SemanticPressureIssueOrigin
    introduced_by_repair_round: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_origin(self) -> SemanticPressureExpectation:
        if self.origin is SemanticPressureIssueOrigin.PREEXISTING:
            if self.introduced_by_repair_round is not None:
                raise ValueError("preexisting issues cannot declare a repair introduction round")
        elif self.introduced_by_repair_round is None:
            raise ValueError("repair-induced issues require introduced_by_repair_round")
        return self


class SemanticPressureRepairDelta(BenchmarkModel):
    repair_round: int = Field(ge=1)
    changed_files: tuple[str, ...] = Field(min_length=1, max_length=64)
    delta_chars: int = Field(ge=1, le=100_000)
    scope: SemanticPressureRepairScope
    verification_passed: bool
    patch_hash_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_hash_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    addressed_expectation_ids: tuple[str, ...] = ()
    introduced_expectation_ids: tuple[str, ...] = ()

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("changed_files must not contain empty paths")
        if len(normalized) != len(set(normalized)):
            raise ValueError("changed_files must be unique")
        for value in normalized:
            if (
                value.startswith(("/", "\\"))
                or "\\" in value
                or any(part == ".." for part in value.split("/"))
            ):
                raise ValueError("changed_files must use repository-relative POSIX paths")
        return normalized

    @model_validator(mode="after")
    def validate_delta(self) -> SemanticPressureRepairDelta:
        if self.patch_hash_before == self.patch_hash_after:
            raise ValueError("repair delta requires distinct before/after patch hashes")
        if len(self.addressed_expectation_ids) != len(set(self.addressed_expectation_ids)):
            raise ValueError("addressed expectation ids must be unique")
        if len(self.introduced_expectation_ids) != len(set(self.introduced_expectation_ids)):
            raise ValueError("introduced expectation ids must be unique")
        return self


class SemanticPressureRunInput(BenchmarkModel):
    case_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    workload: SemanticPressureWorkload
    model: str = Field(min_length=1, max_length=128)
    task_succeeded: bool
    reviews: tuple[ReviewDecision, ...] = Field(min_length=1, max_length=12)
    repair_deltas: tuple[SemanticPressureRepairDelta, ...] = Field(max_length=11)
    expectations: tuple[SemanticPressureExpectation, ...] = Field(min_length=1)
    reviewer_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_run(self) -> SemanticPressureRunInput:
        if len(self.repair_deltas) != max(0, len(self.reviews) - 1):
            raise ValueError("repair_deltas must describe every transition between reviews")
        rounds = [delta.repair_round for delta in self.repair_deltas]
        if rounds != list(range(1, len(self.repair_deltas) + 1)):
            raise ValueError("repair rounds must be contiguous and start at 1")

        expectation_ids = [item.matcher.expectation_id for item in self.expectations]
        if len(expectation_ids) != len(set(expectation_ids)):
            raise ValueError("semantic-pressure expectation ids must be unique")
        known_ids = set(expectation_ids)
        for delta in self.repair_deltas:
            referenced = set(delta.addressed_expectation_ids) | set(
                delta.introduced_expectation_ids
            )
            unknown = referenced - known_ids
            if unknown:
                raise ValueError(f"repair delta references unknown expectations: {unknown}")
        return self


class SemanticPressureIssueEvent(BenchmarkModel):
    review_round: int = Field(ge=1)
    issue_index: int = Field(ge=0)
    issue_class: SemanticPressureIssueClass
    expectation_id: str | None = None
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=4000)


class SemanticPressureRunMetrics(BenchmarkModel):
    case_id: str
    workload: SemanticPressureWorkload
    model: str
    verdict: SemanticPressureVerdict
    evaluable: bool
    closure_ab_eligible: bool
    task_succeeded: bool
    review_rounds: int = Field(ge=1)
    repair_rounds: int = Field(ge=0)
    initial_primary_events: int = Field(ge=0)
    recurring_primary_events: int = Field(ge=0)
    confirmed_churn_events: int = Field(ge=0)
    legitimate_new_blocker_events: int = Field(ge=0)
    repair_induced_issue_events: int = Field(ge=0)
    unregistered_new_issue_events: int = Field(ge=0)
    reviewer_tokens: int = Field(ge=0)
    primary_issue_ids: tuple[str, ...] = ()
    issue_events: tuple[SemanticPressureIssueEvent, ...] = ()


class SemanticPressureBucket(BenchmarkModel):
    dimension: str
    key: str
    sample_count: int = Field(ge=1)
    evaluable_count: int = Field(ge=0)
    confirmed_churn_cases: int = Field(ge=0)
    confirmed_churn_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class SemanticPressureAggregate(BenchmarkModel):
    sample_count: int = Field(ge=1)
    evaluable_count: int = Field(ge=0)
    confirmed_churn_cases: int = Field(ge=0)
    confirmed_churn_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    closure_ab_eligible_case_ids: tuple[str, ...] = ()
    inconclusive_count: int = Field(ge=0)
    buckets: tuple[SemanticPressureBucket, ...] = ()


def _find_expectation(
    issue: ReviewIssue,
    expectations: tuple[SemanticPressureExpectation, ...],
) -> SemanticPressureExpectation | None:
    return next((item for item in expectations if item.matcher.matches(issue)), None)


def _classify_issue(
    *,
    review_round: int,
    expectation: SemanticPressureExpectation | None,
    initial_primary_ids: set[str],
) -> SemanticPressureIssueClass:
    if review_round == 1:
        if expectation is None:
            return SemanticPressureIssueClass.INITIAL_UNMATCHED
        if expectation.matcher.kind is ConvergenceExpectationKind.PRIMARY_BLOCKER:
            return SemanticPressureIssueClass.INITIAL_PRIMARY
        if expectation.matcher.kind is ConvergenceExpectationKind.VALID_NEW_BLOCKER:
            return SemanticPressureIssueClass.INITIAL_VALID_BLOCKER
        return SemanticPressureIssueClass.INITIAL_CHURN_CANDIDATE

    if expectation is None:
        return SemanticPressureIssueClass.UNREGISTERED_NEW_ISSUE
    if expectation.matcher.kind is ConvergenceExpectationKind.PRIMARY_BLOCKER:
        if expectation.matcher.expectation_id in initial_primary_ids:
            return SemanticPressureIssueClass.RECURRING_PRIMARY
        return SemanticPressureIssueClass.LEGITIMATE_NEW_BLOCKER
    if expectation.origin is SemanticPressureIssueOrigin.REPAIR_INDUCED:
        return SemanticPressureIssueClass.REPAIR_INDUCED_ISSUE
    if expectation.matcher.kind is ConvergenceExpectationKind.VALID_NEW_BLOCKER:
        return SemanticPressureIssueClass.LEGITIMATE_NEW_BLOCKER
    return SemanticPressureIssueClass.CONFIRMED_BASELINE_CHURN


def analyze_semantic_pressure(
    run: SemanticPressureRunInput,
) -> SemanticPressureRunMetrics:
    events: list[SemanticPressureIssueEvent] = []
    initial_primary_ids: set[str] = set()

    for review_round, decision in enumerate(run.reviews, start=1):
        for issue_index, issue in enumerate(decision.issues):
            expectation = _find_expectation(issue, run.expectations)
            issue_class = _classify_issue(
                review_round=review_round,
                expectation=expectation,
                initial_primary_ids=initial_primary_ids,
            )
            expectation_id = (
                expectation.matcher.expectation_id if expectation is not None else None
            )
            events.append(
                SemanticPressureIssueEvent(
                    review_round=review_round,
                    issue_index=issue_index,
                    issue_class=issue_class,
                    expectation_id=expectation_id,
                    file=issue.file,
                    line=issue.line,
                    message=issue.message,
                )
            )
            if review_round == 1 and issue_class is SemanticPressureIssueClass.INITIAL_PRIMARY:
                if expectation_id is not None:
                    initial_primary_ids.add(expectation_id)

    count = lambda kind: sum(event.issue_class is kind for event in events)
    initial_primary = count(SemanticPressureIssueClass.INITIAL_PRIMARY)
    initial_churn = count(SemanticPressureIssueClass.INITIAL_CHURN_CANDIDATE)
    recurring_primary = count(SemanticPressureIssueClass.RECURRING_PRIMARY)
    confirmed_churn = count(SemanticPressureIssueClass.CONFIRMED_BASELINE_CHURN)
    legitimate_new = count(SemanticPressureIssueClass.LEGITIMATE_NEW_BLOCKER)
    repair_induced = count(SemanticPressureIssueClass.REPAIR_INDUCED_ISSUE)
    unregistered_new = count(SemanticPressureIssueClass.UNREGISTERED_NEW_ISSUE)

    has_followup = len(run.reviews) >= 2
    verification_intercepted = any(
        not delta.verification_passed for delta in run.repair_deltas
    )
    evaluable = (
        initial_primary > 0
        and initial_churn == 0
        and has_followup
        and not verification_intercepted
    )

    if initial_primary == 0:
        verdict = SemanticPressureVerdict.INCONCLUSIVE_PRIMARY_MISSED
    elif initial_churn > 0:
        verdict = SemanticPressureVerdict.INCONCLUSIVE_INITIAL_CHURN_SEEN
    elif not has_followup:
        verdict = SemanticPressureVerdict.INCONCLUSIVE_NO_FOLLOWUP_REVIEW
    elif verification_intercepted:
        verdict = SemanticPressureVerdict.VERIFICATION_INTERCEPTED
    elif recurring_primary > 0:
        verdict = SemanticPressureVerdict.PRIMARY_UNRESOLVED
    elif repair_induced > 0:
        verdict = SemanticPressureVerdict.REPAIR_INDUCED_REGRESSION
    elif legitimate_new > 0:
        verdict = SemanticPressureVerdict.LEGITIMATE_NEW_BLOCKER
    elif confirmed_churn > 0:
        verdict = SemanticPressureVerdict.BASELINE_CHURN_CONFIRMED
    elif unregistered_new > 0:
        verdict = SemanticPressureVerdict.INCONCLUSIVE_UNREGISTERED_ISSUE
    else:
        verdict = SemanticPressureVerdict.NO_BASELINE_CHURN

    return SemanticPressureRunMetrics(
        case_id=run.case_id,
        workload=run.workload,
        model=run.model,
        verdict=verdict,
        evaluable=evaluable,
        closure_ab_eligible=(
            verdict is SemanticPressureVerdict.BASELINE_CHURN_CONFIRMED
        ),
        task_succeeded=run.task_succeeded,
        review_rounds=len(run.reviews),
        repair_rounds=len(run.repair_deltas),
        initial_primary_events=initial_primary,
        recurring_primary_events=recurring_primary,
        confirmed_churn_events=confirmed_churn,
        legitimate_new_blocker_events=legitimate_new,
        repair_induced_issue_events=repair_induced,
        unregistered_new_issue_events=unregistered_new,
        reviewer_tokens=run.reviewer_tokens,
        primary_issue_ids=tuple(sorted(initial_primary_ids)),
        issue_events=tuple(events),
    )


def _bucket(
    dimension: str,
    key: str,
    metrics: list[SemanticPressureRunMetrics],
) -> SemanticPressureBucket:
    evaluable = [item for item in metrics if item.evaluable]
    churn_cases = sum(item.closure_ab_eligible for item in evaluable)
    return SemanticPressureBucket(
        dimension=dimension,
        key=key,
        sample_count=len(metrics),
        evaluable_count=len(evaluable),
        confirmed_churn_cases=churn_cases,
        confirmed_churn_rate=(
            churn_cases / len(evaluable) if evaluable else None
        ),
    )


def aggregate_semantic_pressure(
    runs: tuple[SemanticPressureRunInput, ...],
) -> SemanticPressureAggregate:
    if not runs:
        raise ValueError("semantic-pressure aggregate requires at least one run")

    metrics = [analyze_semantic_pressure(run) for run in runs]
    evaluable = [item for item in metrics if item.evaluable]
    eligible_ids = tuple(
        sorted(item.case_id for item in metrics if item.closure_ab_eligible)
    )

    buckets: list[SemanticPressureBucket] = []
    for workload in SemanticPressureWorkload:
        members = [item for item in metrics if item.workload is workload]
        if members:
            buckets.append(_bucket("workload", workload.value, members))
    for model in sorted({item.model for item in metrics}):
        members = [item for item in metrics if item.model == model]
        buckets.append(_bucket("model", model, members))

    scope_members: dict[SemanticPressureRepairScope, list[SemanticPressureRunMetrics]] = {}
    for run, metric in zip(runs, metrics, strict=True):
        if not run.repair_deltas:
            continue
        scope_members.setdefault(run.repair_deltas[0].scope, []).append(metric)
    for scope in SemanticPressureRepairScope:
        members = scope_members.get(scope)
        if members:
            buckets.append(_bucket("repair_scope", scope.value, members))

    churn_cases = sum(item.closure_ab_eligible for item in evaluable)
    return SemanticPressureAggregate(
        sample_count=len(metrics),
        evaluable_count=len(evaluable),
        confirmed_churn_cases=churn_cases,
        confirmed_churn_rate=(
            churn_cases / len(evaluable) if evaluable else None
        ),
        closure_ab_eligible_case_ids=eligible_ids,
        inconclusive_count=len(metrics) - len(evaluable),
        buckets=tuple(buckets),
    )
