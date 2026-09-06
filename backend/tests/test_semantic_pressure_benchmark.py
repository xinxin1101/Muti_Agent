from __future__ import annotations

import pytest

from app.benchmark.convergence import (
    ConvergenceExpectationKind,
    ConvergenceIssueExpectation,
)
from app.benchmark.semantic_pressure import (
    SemanticPressureExpectation,
    SemanticPressureIssueOrigin,
    SemanticPressureRepairDelta,
    SemanticPressureRepairScope,
    SemanticPressureRunInput,
    SemanticPressureVerdict,
    SemanticPressureWorkload,
    aggregate_semantic_pressure,
    analyze_semantic_pressure,
)
from app.models.review import ReviewDecision, ReviewIssue, ReviewOutcome, ReviewSeverity


def _issue(message: str, *, file: str = "src/service.py", line: int = 20) -> ReviewIssue:
    return ReviewIssue(
        severity=ReviewSeverity.HIGH,
        message=message,
        file=file,
        line=line,
    )


def _changes(*issues: ReviewIssue) -> ReviewDecision:
    return ReviewDecision(
        decision=ReviewOutcome.CHANGES_REQUESTED,
        summary="changes required",
        issues=list(issues),
    )


def _pass() -> ReviewDecision:
    return ReviewDecision(
        decision=ReviewOutcome.PASS,
        summary="all blockers closed",
        issues=[],
    )


def _expectation(
    expectation_id: str,
    kind: ConvergenceExpectationKind,
    pattern: str,
    *,
    origin: SemanticPressureIssueOrigin = SemanticPressureIssueOrigin.PREEXISTING,
    introduced_by_repair_round: int | None = None,
) -> SemanticPressureExpectation:
    return SemanticPressureExpectation(
        matcher=ConvergenceIssueExpectation(
            expectation_id=expectation_id,
            kind=kind,
            file="src/service.py",
            message_patterns=(pattern,),
        ),
        origin=origin,
        introduced_by_repair_round=introduced_by_repair_round,
    )


def _primary() -> SemanticPressureExpectation:
    return _expectation(
        "primary-transaction",
        ConvergenceExpectationKind.PRIMARY_BLOCKER,
        r"transaction|rollback",
    )


def _churn_candidate() -> SemanticPressureExpectation:
    return _expectation(
        "latent-style",
        ConvergenceExpectationKind.CHURN_TRAP,
        r"naming|style",
    )


def _repair_regression() -> SemanticPressureExpectation:
    return _expectation(
        "repair-null-regression",
        ConvergenceExpectationKind.VALID_NEW_BLOCKER,
        r"null|none dereference",
        origin=SemanticPressureIssueOrigin.REPAIR_INDUCED,
        introduced_by_repair_round=1,
    )


def _delta(
    *,
    verification_passed: bool = True,
    scope: SemanticPressureRepairScope = SemanticPressureRepairScope.STATEFUL,
    introduced_expectation_ids: tuple[str, ...] = (),
) -> SemanticPressureRepairDelta:
    return SemanticPressureRepairDelta(
        repair_round=1,
        changed_files=("src/service.py",),
        delta_chars=420,
        scope=scope,
        verification_passed=verification_passed,
        patch_hash_before="a" * 64,
        patch_hash_after="b" * 64,
        addressed_expectation_ids=("primary-transaction",),
        introduced_expectation_ids=introduced_expectation_ids,
    )


def _run(
    *,
    case_id: str,
    reviews: tuple[ReviewDecision, ...],
    delta: SemanticPressureRepairDelta,
    workload: SemanticPressureWorkload = SemanticPressureWorkload.DATABASE_TRANSACTION,
    model: str = "qwen3.5-flash",
    task_succeeded: bool = True,
    expectations: tuple[SemanticPressureExpectation, ...] | None = None,
) -> SemanticPressureRunInput:
    return SemanticPressureRunInput(
        case_id=case_id,
        workload=workload,
        model=model,
        task_succeeded=task_succeeded,
        reviews=reviews,
        repair_deltas=(delta,),
        expectations=expectations or (_primary(), _churn_candidate(), _repair_regression()),
        reviewer_tokens=6200,
    )


def test_confirms_only_preexisting_late_churn_and_marks_closure_ab_eligible() -> None:
    metrics = analyze_semantic_pressure(
        _run(
            case_id="transaction-late-style",
            reviews=(
                _changes(_issue("transaction does not rollback on failure")),
                _changes(_issue("naming style could be improved", line=80)),
            ),
            delta=_delta(),
        )
    )

    assert metrics.evaluable is True
    assert metrics.verdict is SemanticPressureVerdict.BASELINE_CHURN_CONFIRMED
    assert metrics.confirmed_churn_events == 1
    assert metrics.closure_ab_eligible is True


def test_repair_induced_correctness_issue_is_not_counted_as_churn() -> None:
    metrics = analyze_semantic_pressure(
        _run(
            case_id="transaction-repair-regression",
            reviews=(
                _changes(_issue("transaction does not rollback on failure")),
                _changes(_issue("repair introduces null dereference", line=42)),
            ),
            delta=_delta(introduced_expectation_ids=("repair-null-regression",)),
        )
    )

    assert metrics.evaluable is True
    assert metrics.verdict is SemanticPressureVerdict.REPAIR_INDUCED_REGRESSION
    assert metrics.repair_induced_issue_events == 1
    assert metrics.confirmed_churn_events == 0
    assert metrics.closure_ab_eligible is False


def test_recurring_primary_means_repair_did_not_close_original_blocker() -> None:
    metrics = analyze_semantic_pressure(
        _run(
            case_id="transaction-primary-recurs",
            reviews=(
                _changes(_issue("transaction does not rollback on failure")),
                _changes(_issue("rollback is still missing", line=24)),
            ),
            delta=_delta(),
        )
    )

    assert metrics.verdict is SemanticPressureVerdict.PRIMARY_UNRESOLVED
    assert metrics.recurring_primary_events == 1
    assert metrics.closure_ab_eligible is False


def test_verification_intercepted_transition_is_not_evaluable_for_reviewer_churn() -> None:
    metrics = analyze_semantic_pressure(
        _run(
            case_id="transaction-verification-intercept",
            reviews=(
                _changes(_issue("transaction does not rollback on failure")),
                _pass(),
            ),
            delta=_delta(verification_passed=False),
        )
    )

    assert metrics.evaluable is False
    assert metrics.verdict is SemanticPressureVerdict.VERIFICATION_INTERCEPTED
    assert metrics.closure_ab_eligible is False


def test_initial_churn_candidate_makes_discovery_trajectory_inconclusive() -> None:
    metrics = analyze_semantic_pressure(
        _run(
            case_id="transaction-trap-seen-early",
            reviews=(
                _changes(
                    _issue("transaction does not rollback on failure"),
                    _issue("naming style could be improved", line=80),
                ),
                _pass(),
            ),
            delta=_delta(),
        )
    )

    assert metrics.evaluable is False
    assert metrics.verdict is SemanticPressureVerdict.INCONCLUSIVE_INITIAL_CHURN_SEEN


def test_aggregate_surfaces_workload_model_and_repair_scope_churn_buckets() -> None:
    churn_run = _run(
        case_id="transaction-late-style",
        reviews=(
            _changes(_issue("transaction does not rollback on failure")),
            _changes(_issue("naming style could be improved", line=80)),
        ),
        delta=_delta(scope=SemanticPressureRepairScope.STATEFUL),
    )
    no_churn_run = _run(
        case_id="api-clean-close",
        workload=SemanticPressureWorkload.API_CONTRACT,
        model="second-reviewer-model",
        reviews=(
            _changes(_issue("transaction does not rollback on failure")),
            _pass(),
        ),
        delta=_delta(scope=SemanticPressureRepairScope.CONTRACT_TOUCHING),
    )

    aggregate = aggregate_semantic_pressure((churn_run, no_churn_run))

    assert aggregate.sample_count == 2
    assert aggregate.evaluable_count == 2
    assert aggregate.confirmed_churn_cases == 1
    assert aggregate.confirmed_churn_rate == pytest.approx(0.5)
    assert aggregate.closure_ab_eligible_case_ids == ("transaction-late-style",)

    bucket_map = {(item.dimension, item.key): item for item in aggregate.buckets}
    transaction_bucket = bucket_map[
        ("workload", SemanticPressureWorkload.DATABASE_TRANSACTION.value)
    ]
    assert transaction_bucket.confirmed_churn_rate == 1.0
    assert bucket_map[("model", "second-reviewer-model")].confirmed_churn_rate == 0.0
    assert bucket_map[
        ("repair_scope", SemanticPressureRepairScope.STATEFUL.value)
    ].confirmed_churn_rate == 1.0


def test_repair_induced_expectation_requires_introduction_round() -> None:
    with pytest.raises(ValueError, match="require introduced_by_repair_round"):
        _expectation(
            "bad-induced",
            ConvergenceExpectationKind.VALID_NEW_BLOCKER,
            r"regression",
            origin=SemanticPressureIssueOrigin.REPAIR_INDUCED,
        )
