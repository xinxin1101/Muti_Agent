from __future__ import annotations

import math
import re
from enum import StrEnum
from statistics import mean, median

from pydantic import Field, field_validator, model_validator

from app.benchmark.models import BenchmarkModel
from app.models.review import ReviewDecision, ReviewIssue, ReviewOutcome


class ConvergenceExpectationKind(StrEnum):
    PRIMARY_BLOCKER = "PRIMARY_BLOCKER"
    VALID_NEW_BLOCKER = "VALID_NEW_BLOCKER"
    CHURN_TRAP = "CHURN_TRAP"


class ConvergenceIssueClass(StrEnum):
    INITIAL_PRIMARY = "INITIAL_PRIMARY"
    INITIAL_VALID_BLOCKER = "INITIAL_VALID_BLOCKER"
    INITIAL_CHURN_TRAP = "INITIAL_CHURN_TRAP"
    INITIAL_UNMATCHED = "INITIAL_UNMATCHED"
    RECURRING_PRIMARY = "RECURRING_PRIMARY"
    LATE_PRIMARY = "LATE_PRIMARY"
    VALID_NEW_BLOCKER = "VALID_NEW_BLOCKER"
    CONFIRMED_CHURN = "CONFIRMED_CHURN"
    UNEXPECTED_NEW_ISSUE = "UNEXPECTED_NEW_ISSUE"
    REPEATED_UNMATCHED = "REPEATED_UNMATCHED"


class ConvergencePairVerdict(StrEnum):
    IMPROVED = "IMPROVED"
    EQUIVALENT = "EQUIVALENT"
    EQUIVALENT_WITH_OVERHEAD = "EQUIVALENT_WITH_OVERHEAD"
    REGRESSED = "REGRESSED"
    INCONCLUSIVE = "INCONCLUSIVE"


class ConvergenceIssueExpectation(BenchmarkModel):
    expectation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    kind: ConvergenceExpectationKind
    file: str | None = Field(default=None, min_length=1, max_length=512)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    message_patterns: tuple[str, ...] = Field(min_length=1, max_length=16)
    minimum_pattern_matches: int = Field(default=1, ge=1, le=16)

    @field_validator("message_patterns")
    @classmethod
    def validate_patterns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("convergence issue patterns must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("convergence issue patterns must not contain duplicates")
        for value in normalized:
            try:
                re.compile(value, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid convergence issue regex: {value}") from exc
        return normalized

    @field_validator("file")
    @classmethod
    def validate_file(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if (
            normalized.startswith(("/", "\\"))
            or "\\" in normalized
            or any(part == ".." for part in normalized.split("/"))
        ):
            raise ValueError("convergence issue file must be a repository-relative POSIX path")
        return normalized

    @model_validator(mode="after")
    def validate_bounds(self) -> ConvergenceIssueExpectation:
        if self.line_start is None and self.line_end is not None:
            raise ValueError("line_end requires line_start")
        if (
            self.line_end is not None
            and self.line_start is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end must be greater than or equal to line_start")
        if self.minimum_pattern_matches > len(self.message_patterns):
            raise ValueError("minimum_pattern_matches exceeds configured patterns")
        return self

    def matches(self, issue: ReviewIssue) -> bool:
        if self.file is not None and issue.file != self.file:
            return False
        if self.line_start is not None:
            if issue.line is None:
                return False
            upper = self.line_end if self.line_end is not None else self.line_start
            if not self.line_start <= issue.line <= upper:
                return False
        matches = sum(
            1
            for pattern in self.message_patterns
            if re.search(pattern, issue.message, re.IGNORECASE) is not None
        )
        return matches >= self.minimum_pattern_matches


class ConvergenceRunInput(BenchmarkModel):
    case_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    variant: str = Field(min_length=1, max_length=64)
    task_succeeded: bool
    reviews: tuple[ReviewDecision, ...]
    repair_attempts: int = Field(ge=0)
    reviewer_tokens: int = Field(ge=0)
    repair_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    expectations: tuple[ConvergenceIssueExpectation, ...] = ()

    @model_validator(mode="after")
    def validate_input(self) -> ConvergenceRunInput:
        expectation_ids = [item.expectation_id for item in self.expectations]
        if len(expectation_ids) != len(set(expectation_ids)):
            raise ValueError("convergence expectation ids must be unique")
        if self.repair_attempts == 0 and len(self.reviews) > 1:
            raise ValueError("multiple review rounds require at least one repair attempt")
        return self


class ConvergenceIssueEvent(BenchmarkModel):
    review_round: int = Field(ge=1)
    issue_index: int = Field(ge=0)
    issue_class: ConvergenceIssueClass
    expectation_id: str | None = None
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=4000)


class ConvergenceRunMetrics(BenchmarkModel):
    case_id: str
    variant: str
    task_succeeded: bool
    final_review_passed: bool
    converged: bool
    review_rounds: int = Field(ge=0)
    review_rejections: int = Field(ge=0)
    completed_review_repair_cycles: int = Field(ge=0)
    extra_review_repair_cycles: int = Field(ge=0)
    repair_attempts: int = Field(ge=0)
    primary_recurrence_events: int = Field(ge=0)
    late_primary_events: int = Field(ge=0)
    valid_new_blocker_events: int = Field(ge=0)
    confirmed_churn_events: int = Field(ge=0)
    unexpected_new_issue_events: int = Field(ge=0)
    initial_churn_trap_events: int = Field(ge=0)
    reviewer_tokens: int = Field(ge=0)
    repair_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    issue_events: tuple[ConvergenceIssueEvent, ...] = ()

    @property
    def has_confirmed_churn(self) -> bool:
        return self.confirmed_churn_events > 0


class ConvergencePairDelta(BenchmarkModel):
    case_id: str
    baseline_variant: str
    closure_variant: str
    verdict: ConvergencePairVerdict
    baseline_converged: bool
    closure_converged: bool
    confirmed_churn_delta: int
    extra_cycle_delta: int
    repair_attempt_delta: int
    reviewer_token_delta: int
    repair_token_delta: int
    total_token_delta: int
    total_token_delta_pct: float | None = None


class ConvergenceAggregate(BenchmarkModel):
    sample_count: int = Field(ge=1)
    baseline_success_rate: float = Field(ge=0.0, le=1.0)
    closure_success_rate: float = Field(ge=0.0, le=1.0)
    baseline_confirmed_churn_case_rate: float = Field(ge=0.0, le=1.0)
    closure_confirmed_churn_case_rate: float = Field(ge=0.0, le=1.0)
    churn_pair_improvements: int = Field(ge=0)
    churn_pair_regressions: int = Field(ge=0)
    churn_mcnemar_exact_p: float | None = Field(default=None, ge=0.0, le=1.0)
    cycle_pair_wins: int = Field(ge=0)
    cycle_pair_losses: int = Field(ge=0)
    cycle_sign_test_exact_p: float | None = Field(default=None, ge=0.0, le=1.0)
    improved_pairs: int = Field(ge=0)
    regressed_pairs: int = Field(ge=0)
    equivalent_pairs: int = Field(ge=0)
    overhead_only_pairs: int = Field(ge=0)
    inconclusive_pairs: int = Field(ge=0)
    mean_total_token_delta: float
    median_total_token_delta: float
    mean_total_token_delta_pct: float | None = None


def _issue_key(issue: ReviewIssue) -> tuple[str | None, int | None, str]:
    normalized_message = " ".join(issue.message.casefold().split())
    return issue.file, issue.line, normalized_message


def _match_expectation(
    issue: ReviewIssue,
    expectations: tuple[ConvergenceIssueExpectation, ...],
) -> ConvergenceIssueExpectation | None:
    return next((expectation for expectation in expectations if expectation.matches(issue)), None)


def _classify_issue(
    *,
    issue: ReviewIssue,
    review_round: int,
    expectation: ConvergenceIssueExpectation | None,
    initial_expectation_ids: set[str],
    seen_unmatched: set[tuple[str | None, int | None, str]],
) -> ConvergenceIssueClass:
    if review_round == 1:
        if expectation is None:
            return ConvergenceIssueClass.INITIAL_UNMATCHED
        if expectation.kind is ConvergenceExpectationKind.PRIMARY_BLOCKER:
            return ConvergenceIssueClass.INITIAL_PRIMARY
        if expectation.kind is ConvergenceExpectationKind.VALID_NEW_BLOCKER:
            return ConvergenceIssueClass.INITIAL_VALID_BLOCKER
        return ConvergenceIssueClass.INITIAL_CHURN_TRAP

    if expectation is not None:
        if expectation.kind is ConvergenceExpectationKind.PRIMARY_BLOCKER:
            if expectation.expectation_id in initial_expectation_ids:
                return ConvergenceIssueClass.RECURRING_PRIMARY
            return ConvergenceIssueClass.LATE_PRIMARY
        if expectation.kind is ConvergenceExpectationKind.VALID_NEW_BLOCKER:
            return ConvergenceIssueClass.VALID_NEW_BLOCKER
        return ConvergenceIssueClass.CONFIRMED_CHURN

    if _issue_key(issue) in seen_unmatched:
        return ConvergenceIssueClass.REPEATED_UNMATCHED
    return ConvergenceIssueClass.UNEXPECTED_NEW_ISSUE


def analyze_convergence(run: ConvergenceRunInput) -> ConvergenceRunMetrics:
    initial_expectation_ids: set[str] = set()
    seen_unmatched: set[tuple[str | None, int | None, str]] = set()
    events: list[ConvergenceIssueEvent] = []

    for review_round, decision in enumerate(run.reviews, start=1):
        for issue_index, issue in enumerate(decision.issues):
            expectation = _match_expectation(issue, run.expectations)
            issue_class = _classify_issue(
                issue=issue,
                review_round=review_round,
                expectation=expectation,
                initial_expectation_ids=initial_expectation_ids,
                seen_unmatched=seen_unmatched,
            )
            events.append(
                ConvergenceIssueEvent(
                    review_round=review_round,
                    issue_index=issue_index,
                    issue_class=issue_class,
                    expectation_id=(
                        expectation.expectation_id if expectation is not None else None
                    ),
                    file=issue.file,
                    line=issue.line,
                    message=issue.message,
                )
            )
            if review_round == 1 and expectation is not None:
                initial_expectation_ids.add(expectation.expectation_id)
            if expectation is None:
                seen_unmatched.add(_issue_key(issue))

    review_rounds = len(run.reviews)
    review_rejections = sum(
        1 for decision in run.reviews if decision.decision is ReviewOutcome.CHANGES_REQUESTED
    )
    completed_cycles = sum(
        1
        for index, decision in enumerate(run.reviews[:-1])
        if decision.decision is ReviewOutcome.CHANGES_REQUESTED
        and index + 1 < review_rounds
    )
    final_pass = bool(run.reviews) and run.reviews[-1].decision is ReviewOutcome.PASS

    return ConvergenceRunMetrics(
        case_id=run.case_id,
        variant=run.variant,
        task_succeeded=run.task_succeeded,
        final_review_passed=final_pass,
        converged=run.task_succeeded and final_pass,
        review_rounds=review_rounds,
        review_rejections=review_rejections,
        completed_review_repair_cycles=completed_cycles,
        extra_review_repair_cycles=max(0, completed_cycles - 1),
        repair_attempts=run.repair_attempts,
        primary_recurrence_events=sum(
            event.issue_class is ConvergenceIssueClass.RECURRING_PRIMARY for event in events
        ),
        late_primary_events=sum(
            event.issue_class is ConvergenceIssueClass.LATE_PRIMARY for event in events
        ),
        valid_new_blocker_events=sum(
            event.issue_class is ConvergenceIssueClass.VALID_NEW_BLOCKER for event in events
        ),
        confirmed_churn_events=sum(
            event.issue_class is ConvergenceIssueClass.CONFIRMED_CHURN for event in events
        ),
        unexpected_new_issue_events=sum(
            event.issue_class is ConvergenceIssueClass.UNEXPECTED_NEW_ISSUE for event in events
        ),
        initial_churn_trap_events=sum(
            event.issue_class is ConvergenceIssueClass.INITIAL_CHURN_TRAP for event in events
        ),
        reviewer_tokens=run.reviewer_tokens,
        repair_tokens=run.repair_tokens,
        total_tokens=run.total_tokens,
        issue_events=tuple(events),
    )


def compare_convergence_pair(
    baseline: ConvergenceRunMetrics,
    closure: ConvergenceRunMetrics,
) -> ConvergencePairDelta:
    if baseline.case_id != closure.case_id:
        raise ValueError("paired convergence metrics must use the same case_id")

    churn_delta = closure.confirmed_churn_events - baseline.confirmed_churn_events
    cycle_delta = closure.extra_review_repair_cycles - baseline.extra_review_repair_cycles
    token_delta = closure.total_tokens - baseline.total_tokens
    token_delta_pct = token_delta / baseline.total_tokens if baseline.total_tokens else None

    if baseline.converged and not closure.converged:
        verdict = ConvergencePairVerdict.REGRESSED
    elif not baseline.converged and closure.converged:
        verdict = ConvergencePairVerdict.IMPROVED
    elif churn_delta > 0 or cycle_delta > 0:
        verdict = ConvergencePairVerdict.REGRESSED
    elif churn_delta < 0 or cycle_delta < 0:
        verdict = ConvergencePairVerdict.IMPROVED
    elif baseline.converged == closure.converged:
        verdict = (
            ConvergencePairVerdict.EQUIVALENT_WITH_OVERHEAD
            if token_delta > 0
            else ConvergencePairVerdict.EQUIVALENT
        )
    else:
        verdict = ConvergencePairVerdict.INCONCLUSIVE

    return ConvergencePairDelta(
        case_id=baseline.case_id,
        baseline_variant=baseline.variant,
        closure_variant=closure.variant,
        verdict=verdict,
        baseline_converged=baseline.converged,
        closure_converged=closure.converged,
        confirmed_churn_delta=churn_delta,
        extra_cycle_delta=cycle_delta,
        repair_attempt_delta=closure.repair_attempts - baseline.repair_attempts,
        reviewer_token_delta=closure.reviewer_tokens - baseline.reviewer_tokens,
        repair_token_delta=closure.repair_tokens - baseline.repair_tokens,
        total_token_delta=token_delta,
        total_token_delta_pct=token_delta_pct,
    )


def _exact_two_sided_sign_test(wins: int, losses: int) -> float | None:
    discordant = wins + losses
    if discordant == 0:
        return None
    smaller = min(wins, losses)
    cumulative = sum(math.comb(discordant, k) for k in range(smaller + 1))
    return min(1.0, 2.0 * cumulative / (2**discordant))


def aggregate_convergence_pairs(
    pairs: tuple[tuple[ConvergenceRunMetrics, ConvergenceRunMetrics], ...],
) -> ConvergenceAggregate:
    if not pairs:
        raise ValueError("convergence aggregate requires at least one paired sample")

    deltas = tuple(compare_convergence_pair(baseline, closure) for baseline, closure in pairs)
    baseline_runs = tuple(item[0] for item in pairs)
    closure_runs = tuple(item[1] for item in pairs)

    churn_improvements = sum(
        baseline.has_confirmed_churn and not closure.has_confirmed_churn
        for baseline, closure in pairs
    )
    churn_regressions = sum(
        not baseline.has_confirmed_churn and closure.has_confirmed_churn
        for baseline, closure in pairs
    )
    cycle_wins = sum(
        closure.extra_review_repair_cycles < baseline.extra_review_repair_cycles
        for baseline, closure in pairs
    )
    cycle_losses = sum(
        closure.extra_review_repair_cycles > baseline.extra_review_repair_cycles
        for baseline, closure in pairs
    )
    token_delta_pcts = [
        delta.total_token_delta_pct
        for delta in deltas
        if delta.total_token_delta_pct is not None
    ]

    sample_count = len(pairs)
    equivalent = sum(delta.verdict is ConvergencePairVerdict.EQUIVALENT for delta in deltas)
    overhead_only = sum(
        delta.verdict is ConvergencePairVerdict.EQUIVALENT_WITH_OVERHEAD for delta in deltas
    )

    return ConvergenceAggregate(
        sample_count=sample_count,
        baseline_success_rate=sum(item.converged for item in baseline_runs) / sample_count,
        closure_success_rate=sum(item.converged for item in closure_runs) / sample_count,
        baseline_confirmed_churn_case_rate=(
            sum(item.has_confirmed_churn for item in baseline_runs) / sample_count
        ),
        closure_confirmed_churn_case_rate=(
            sum(item.has_confirmed_churn for item in closure_runs) / sample_count
        ),
        churn_pair_improvements=churn_improvements,
        churn_pair_regressions=churn_regressions,
        churn_mcnemar_exact_p=_exact_two_sided_sign_test(
            churn_improvements,
            churn_regressions,
        ),
        cycle_pair_wins=cycle_wins,
        cycle_pair_losses=cycle_losses,
        cycle_sign_test_exact_p=_exact_two_sided_sign_test(cycle_wins, cycle_losses),
        improved_pairs=sum(delta.verdict is ConvergencePairVerdict.IMPROVED for delta in deltas),
        regressed_pairs=sum(delta.verdict is ConvergencePairVerdict.REGRESSED for delta in deltas),
        equivalent_pairs=equivalent,
        overhead_only_pairs=overhead_only,
        inconclusive_pairs=sum(
            delta.verdict is ConvergencePairVerdict.INCONCLUSIVE for delta in deltas
        ),
        mean_total_token_delta=mean(delta.total_token_delta for delta in deltas),
        median_total_token_delta=median(delta.total_token_delta for delta in deltas),
        mean_total_token_delta_pct=(mean(token_delta_pcts) if token_delta_pcts else None),
    )
