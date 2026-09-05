from __future__ import annotations

import pytest

from app.benchmark import (
    ConvergenceExpectationKind,
    ConvergenceIssueExpectation,
    ConvergencePairVerdict,
    ConvergenceRunInput,
    aggregate_convergence_pairs,
    analyze_convergence,
    compare_convergence_pair,
)
from app.models.review import ReviewDecision, ReviewIssue, ReviewOutcome, ReviewSeverity


def _issue(message: str, *, file: str = "src/ui.js", line: int = 20) -> ReviewIssue:
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


def _primary() -> ConvergenceIssueExpectation:
    return ConvergenceIssueExpectation(
        expectation_id="primary",
        kind=ConvergenceExpectationKind.PRIMARY_BLOCKER,
        file="src/ui.js",
        line_start=15,
        line_end=30,
        message_patterns=(r"restart|重新开始", r"duplicate|重复"),
        minimum_pattern_matches=2,
    )


def _trap() -> ConvergenceIssueExpectation:
    return ConvergenceIssueExpectation(
        expectation_id="style-trap",
        kind=ConvergenceExpectationKind.CHURN_TRAP,
        file="src/ui.js",
        message_patterns=(r"naming|命名", r"style|风格"),
    )


def _valid_new() -> ConvergenceIssueExpectation:
    return ConvergenceIssueExpectation(
        expectation_id="repair-regression",
        kind=ConvergenceExpectationKind.VALID_NEW_BLOCKER,
        file="src/ui.js",
        message_patterns=(r"null|missing element|不存在",),
    )


def _run(
    *,
    variant: str,
    reviews: tuple[ReviewDecision, ...],
    repair_attempts: int,
    total_tokens: int,
    task_succeeded: bool = True,
    reviewer_tokens: int = 3000,
    repair_tokens: int = 2000,
):
    return analyze_convergence(
        ConvergenceRunInput(
            case_id="gomoku-duplicate-restart",
            variant=variant,
            task_succeeded=task_succeeded,
            reviews=reviews,
            repair_attempts=repair_attempts,
            reviewer_tokens=reviewer_tokens,
            repair_tokens=repair_tokens,
            total_tokens=total_tokens,
            expectations=(_primary(), _trap(), _valid_new()),
        )
    )


def test_issue_expectation_rejects_unsafe_paths_and_invalid_regex() -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        ConvergenceIssueExpectation(
            expectation_id="unsafe",
            kind=ConvergenceExpectationKind.PRIMARY_BLOCKER,
            file="../ui.js",
            message_patterns=("restart",),
        )

    with pytest.raises(ValueError, match="invalid convergence issue regex"):
        ConvergenceIssueExpectation(
            expectation_id="bad-regex",
            kind=ConvergenceExpectationKind.PRIMARY_BLOCKER,
            message_patterns=("[",),
        )


def test_analyzer_counts_confirmed_churn_and_extra_cycle() -> None:
    metrics = _run(
        variant="baseline",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("style naming should be cleaned up", line=80)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=12000,
    )

    assert metrics.converged is True
    assert metrics.review_rounds == 3
    assert metrics.review_rejections == 2
    assert metrics.completed_review_repair_cycles == 2
    assert metrics.extra_review_repair_cycles == 1
    assert metrics.confirmed_churn_events == 1
    assert metrics.primary_recurrence_events == 0


def test_analyzer_does_not_call_repair_regression_churn() -> None:
    metrics = _run(
        variant="closure",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("missing element null dereference", line=55)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=11000,
    )

    assert metrics.valid_new_blocker_events == 1
    assert metrics.confirmed_churn_events == 0
    assert metrics.extra_review_repair_cycles == 1


def test_analyzer_distinguishes_recurring_primary_from_churn() -> None:
    metrics = _run(
        variant="closure",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("restart button remains duplicate", line=22)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=10000,
    )

    assert metrics.primary_recurrence_events == 1
    assert metrics.confirmed_churn_events == 0


def test_unmatched_new_issue_is_reported_but_not_confirmed_churn() -> None:
    metrics = _run(
        variant="closure",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("canvas sizing concern", line=60)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=10000,
    )

    assert metrics.unexpected_new_issue_events == 1
    assert metrics.confirmed_churn_events == 0


def test_pair_marks_fewer_churn_cycles_as_improved_even_with_token_overhead() -> None:
    baseline = _run(
        variant="baseline",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("style naming should be cleaned up", line=80)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=10000,
    )
    closure = _run(
        variant="closure",
        reviews=(_changes(_issue("duplicate restart button")), _pass()),
        repair_attempts=1,
        total_tokens=10500,
    )

    delta = compare_convergence_pair(baseline, closure)

    assert delta.verdict is ConvergencePairVerdict.IMPROVED
    assert delta.confirmed_churn_delta == -1
    assert delta.extra_cycle_delta == -1
    assert delta.total_token_delta == 500
    assert delta.total_token_delta_pct == pytest.approx(0.05)


def test_pair_marks_same_convergence_with_more_tokens_as_overhead_only() -> None:
    baseline = _run(
        variant="baseline",
        reviews=(_changes(_issue("duplicate restart button")), _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )
    closure = _run(
        variant="closure",
        reviews=(_changes(_issue("duplicate restart button")), _pass()),
        repair_attempts=1,
        total_tokens=10900,
    )

    delta = compare_convergence_pair(baseline, closure)

    assert delta.verdict is ConvergencePairVerdict.EQUIVALENT_WITH_OVERHEAD
    assert delta.confirmed_churn_delta == 0
    assert delta.extra_cycle_delta == 0
    assert delta.total_token_delta_pct == pytest.approx(0.09)


def test_pair_marks_success_regression_as_regressed() -> None:
    baseline = _run(
        variant="baseline",
        reviews=(_changes(_issue("duplicate restart button")), _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )
    closure = _run(
        variant="closure",
        reviews=(_changes(_issue("duplicate restart button")),),
        repair_attempts=1,
        total_tokens=9000,
        task_succeeded=False,
    )

    delta = compare_convergence_pair(baseline, closure)

    assert delta.verdict is ConvergencePairVerdict.REGRESSED


def test_aggregate_reports_paired_churn_and_cycle_significance() -> None:
    pairs = []
    for index in range(3):
        baseline = _run(
            variant=f"baseline-{index}",
            reviews=(
                _changes(_issue("duplicate restart button")),
                _changes(_issue("style naming should be cleaned up", line=80)),
                _pass(),
            ),
            repair_attempts=2,
            total_tokens=10000 + index,
        )
        closure = _run(
            variant=f"closure-{index}",
            reviews=(_changes(_issue("duplicate restart button")), _pass()),
            repair_attempts=1,
            total_tokens=9500 + index,
        )
        pairs.append((baseline, closure))

    aggregate = aggregate_convergence_pairs(tuple(pairs))

    assert aggregate.sample_count == 3
    assert aggregate.baseline_confirmed_churn_case_rate == 1.0
    assert aggregate.closure_confirmed_churn_case_rate == 0.0
    assert aggregate.churn_pair_improvements == 3
    assert aggregate.churn_pair_regressions == 0
    assert aggregate.churn_mcnemar_exact_p == pytest.approx(0.25)
    assert aggregate.cycle_pair_wins == 3
    assert aggregate.cycle_pair_losses == 0
    assert aggregate.cycle_sign_test_exact_p == pytest.approx(0.25)
    assert aggregate.improved_pairs == 3
    assert aggregate.regressed_pairs == 0


def test_pair_is_inconclusive_when_primary_is_not_observed() -> None:
    baseline = _run(
        variant="baseline",
        reviews=(_changes(_issue("canvas sizing concern", line=60)), _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )
    closure = _run(
        variant="closure",
        reviews=(_changes(_issue("canvas sizing concern", line=60)), _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )

    delta = compare_convergence_pair(baseline, closure)

    assert baseline.evaluable is False
    assert closure.evaluable is False
    assert delta.verdict is ConvergencePairVerdict.INCONCLUSIVE


def test_pair_is_inconclusive_when_trap_is_already_in_initial_review() -> None:
    initial = _changes(
        _issue("duplicate restart button"),
        _issue("style naming should be cleaned up", line=80),
    )
    baseline = _run(
        variant="baseline",
        reviews=(initial, _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )
    closure = _run(
        variant="closure",
        reviews=(initial, _pass()),
        repair_attempts=1,
        total_tokens=10000,
    )

    delta = compare_convergence_pair(baseline, closure)

    assert baseline.initial_primary_events == 1
    assert baseline.initial_churn_trap_events == 1
    assert baseline.evaluable is False
    assert delta.verdict is ConvergencePairVerdict.INCONCLUSIVE


def test_aggregate_excludes_inconclusive_pairs_from_churn_statistics() -> None:
    valid_baseline = _run(
        variant="baseline-valid",
        reviews=(
            _changes(_issue("duplicate restart button")),
            _changes(_issue("style naming should be cleaned up", line=80)),
            _pass(),
        ),
        repair_attempts=2,
        total_tokens=10000,
    )
    valid_closure = _run(
        variant="closure-valid",
        reviews=(_changes(_issue("duplicate restart button")), _pass()),
        repair_attempts=1,
        total_tokens=9500,
    )
    invalid_baseline = _run(
        variant="baseline-invalid",
        reviews=(_changes(_issue("canvas sizing concern", line=60)), _pass()),
        repair_attempts=1,
        total_tokens=8000,
    )
    invalid_closure = _run(
        variant="closure-invalid",
        reviews=(_changes(_issue("canvas sizing concern", line=60)), _pass()),
        repair_attempts=1,
        total_tokens=7000,
    )

    aggregate = aggregate_convergence_pairs(
        ((valid_baseline, valid_closure), (invalid_baseline, invalid_closure))
    )

    assert aggregate.sample_count == 2
    assert aggregate.evaluable_pair_count == 1
    assert aggregate.baseline_confirmed_churn_case_rate == 1.0
    assert aggregate.closure_confirmed_churn_case_rate == 0.0
    assert aggregate.inconclusive_pairs == 1
    assert aggregate.mean_total_token_delta == -500


def test_aggregate_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="at least one paired sample"):
        aggregate_convergence_pairs(())
