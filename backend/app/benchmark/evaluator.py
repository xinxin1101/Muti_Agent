from __future__ import annotations

from app.benchmark.io import canonical_sha256
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkCaseEvaluation,
    BenchmarkCaseVerdict,
    BenchmarkChangedFilesMode,
    BenchmarkDataAvailability,
    BenchmarkDimensionResult,
    BenchmarkDimensionStatus,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkReport,
    BenchmarkSummary,
    BenchmarkSuite,
)


def _result(
    status: BenchmarkDimensionStatus,
    *,
    expected: str,
    observed: str,
) -> BenchmarkDimensionResult:
    return BenchmarkDimensionResult(
        status=status,
        expected=expected,
        observed=observed,
    )


def _not_evaluated(*, expected: str, observed: str) -> BenchmarkDimensionResult:
    return _result(
        BenchmarkDimensionStatus.NOT_EVALUATED,
        expected=expected,
        observed=observed,
    )


def _evaluate_terminal_case(
    case: BenchmarkCase,
    observation: BenchmarkObservation,
) -> BenchmarkCaseEvaluation:
    expectations = case.expectations
    failure_modes: list[str] = []

    completion_match = observation.run_status is expectations.terminal_status
    completion = _result(
        BenchmarkDimensionStatus.MATCH if completion_match else BenchmarkDimensionStatus.MISMATCH,
        expected=expectations.terminal_status.value,
        observed=observation.run_status.value if observation.run_status is not None else "MISSING",
    )
    if not completion_match:
        failure_modes.append("TERMINAL_STATUS_MISMATCH")

    required = set(expectations.required_evidence_kinds)
    observed_kinds = set(observation.evidence_kinds)
    missing = sorted((kind.value for kind in required - observed_kinds))
    evidence_match = not missing
    evidence = _result(
        BenchmarkDimensionStatus.MATCH if evidence_match else BenchmarkDimensionStatus.MISMATCH,
        expected=(
            "required="
            + ",".join(sorted(kind.value for kind in expectations.required_evidence_kinds))
        ),
        observed=(
            "observed="
            + ",".join(sorted(kind.value for kind in observation.evidence_kinds))
            + (f"; missing={','.join(missing)}" if missing else "")
        ),
    )
    if not evidence_match:
        failure_modes.append("EVIDENCE_REQUIREMENT_MISMATCH")

    expected_files = set(expectations.changed_files)
    if observation.diff is None:
        code_delta = _not_evaluated(
            expected=(
                f"{expectations.changed_files_mode.value}:"
                + ",".join(expectations.changed_files)
            ),
            observed="diff unavailable",
        )
        failure_modes.append("DIFF_NOT_AVAILABLE")
    elif not observation.diff.complete:
        code_delta = _not_evaluated(
            expected=(
                f"{expectations.changed_files_mode.value}:"
                + ",".join(expectations.changed_files)
            ),
            observed=(
                "bounded diff incomplete; "
                f"changed_file_count={observation.diff.changed_file_count}; "
                f"rendered={len(observation.diff.changed_files)}; "
                f"omitted={observation.diff.omitted_file_count}; "
                f"truncated={observation.diff.truncated}"
            ),
        )
        failure_modes.append("DIFF_INCOMPLETE")
    else:
        observed_files = set(observation.diff.changed_files)
        if expectations.changed_files_mode is BenchmarkChangedFilesMode.EXACT:
            code_match = observed_files == expected_files
        else:
            code_match = expected_files.issubset(observed_files)
        code_delta = _result(
            BenchmarkDimensionStatus.MATCH if code_match else BenchmarkDimensionStatus.MISMATCH,
            expected=(
                f"{expectations.changed_files_mode.value}:"
                + ",".join(sorted(expected_files))
            ),
            observed="changed=" + ",".join(sorted(observed_files)),
        )
        if not code_match:
            failure_modes.append("CODE_DELTA_MISMATCH")

    reliability_checks: list[tuple[str, bool, str, str]] = []
    if expectations.max_repair_attempts is not None:
        observed_repairs = observation.evidence.repair_attempts if observation.evidence else 0
        reliability_checks.append(
            (
                "repair_attempts",
                observed_repairs <= expectations.max_repair_attempts,
                f"<= {expectations.max_repair_attempts}",
                str(observed_repairs),
            )
        )
    if expectations.max_human_decisions is not None:
        observed_human = observation.evidence.human_decisions if observation.evidence else 0
        reliability_checks.append(
            (
                "human_decisions",
                observed_human <= expectations.max_human_decisions,
                f"<= {expectations.max_human_decisions}",
                str(observed_human),
            )
        )
    if reliability_checks:
        reliability_match = all(item[1] for item in reliability_checks)
        reliability = _result(
            (
                BenchmarkDimensionStatus.MATCH
                if reliability_match
                else BenchmarkDimensionStatus.MISMATCH
            ),
            expected="; ".join(f"{name} {expected}" for name, _, expected, _ in reliability_checks),
            observed="; ".join(f"{name}={observed}" for name, _, _, observed in reliability_checks),
        )
        if not reliability_match:
            failure_modes.append("RELIABILITY_BUDGET_MISMATCH")
    else:
        reliability = _not_evaluated(
            expected="no reliability budget configured",
            observed="not compared",
        )

    if expectations.max_terminal_duration_ms is None:
        latency = _not_evaluated(
            expected="no latency budget configured",
            observed=(
                str(observation.terminal_duration_ms)
                if observation.terminal_duration_ms is not None
                else "missing"
            ),
        )
    else:
        duration = observation.terminal_duration_ms
        latency_match = duration is not None and duration <= expectations.max_terminal_duration_ms
        latency = _result(
            BenchmarkDimensionStatus.MATCH if latency_match else BenchmarkDimensionStatus.MISMATCH,
            expected=f"<= {expectations.max_terminal_duration_ms} ms",
            observed=f"{duration} ms" if duration is not None else "missing",
        )
        if not latency_match:
            failure_modes.append("LATENCY_BUDGET_MISMATCH")

    required_dimensions = (completion, evidence, code_delta)
    optional_dimensions = (reliability, latency)
    all_dimensions = (*required_dimensions, *optional_dimensions)
    if any(
        item.status is BenchmarkDimensionStatus.MISMATCH
        for item in all_dimensions
    ):
        verdict = BenchmarkCaseVerdict.MISMATCHED
    elif any(item.status is BenchmarkDimensionStatus.NOT_EVALUATED for item in required_dimensions):
        verdict = BenchmarkCaseVerdict.NOT_EVALUATED
    else:
        verdict = BenchmarkCaseVerdict.MATCHED

    return BenchmarkCaseEvaluation(
        case_id=case.case_id,
        runtime_status=observation.run_status,
        observation_state=observation.state,
        verdict=verdict,
        completion=completion,
        evidence=evidence,
        code_delta=code_delta,
        reliability=reliability,
        latency=latency,
        cost_data=BenchmarkDataAvailability.NOT_AVAILABLE,
        failure_modes=tuple(failure_modes),
    )


def _evaluate_non_terminal_case(
    case: BenchmarkCase,
    observation: BenchmarkObservation,
) -> BenchmarkCaseEvaluation:
    failure_code = (
        observation.failure.code
        if observation.failure is not None
        else observation.state.value
    )
    observed = f"{observation.state.value}:{failure_code}"
    return BenchmarkCaseEvaluation(
        case_id=case.case_id,
        runtime_status=observation.run_status,
        observation_state=observation.state,
        verdict=BenchmarkCaseVerdict.NOT_EVALUATED,
        completion=_not_evaluated(
            expected=case.expectations.terminal_status.value,
            observed=observed,
        ),
        evidence=_not_evaluated(
            expected="required typed evidence",
            observed=observed,
        ),
        code_delta=_not_evaluated(
            expected=(
                f"{case.expectations.changed_files_mode.value}:"
                + ",".join(case.expectations.changed_files)
            ),
            observed=observed,
        ),
        reliability=_not_evaluated(
            expected="configured reliability budgets",
            observed=observed,
        ),
        latency=_not_evaluated(
            expected="configured latency budget",
            observed=observed,
        ),
        cost_data=BenchmarkDataAvailability.NOT_AVAILABLE,
        failure_modes=(failure_code,),
    )


def evaluate_suite(
    suite: BenchmarkSuite,
    suite_sha256: str,
    bundle: BenchmarkObservationBundle,
) -> BenchmarkReport:
    """Deterministically compare observations without mutating runtime truth."""

    if (
        bundle.suite_id != suite.suite_id
        or bundle.suite_version != suite.suite_version
        or bundle.suite_sha256 != suite_sha256
    ):
        raise ValueError("benchmark observations do not belong to the supplied suite")

    expected_case_ids = tuple(case.case_id for case in suite.cases)
    observed_case_ids = tuple(item.case_id for item in bundle.observations)
    if observed_case_ids != expected_case_ids:
        raise ValueError(
            "benchmark observations must match suite case order exactly; "
            f"expected={expected_case_ids!r}, observed={observed_case_ids!r}"
        )

    evaluations = tuple(
        (
            _evaluate_terminal_case(case, observation)
            if observation.state is BenchmarkObservationState.TERMINAL
            else _evaluate_non_terminal_case(case, observation)
        )
        for case, observation in zip(suite.cases, bundle.observations, strict=True)
    )

    summary = BenchmarkSummary(
        total_cases=len(evaluations),
        matched_cases=sum(item.verdict is BenchmarkCaseVerdict.MATCHED for item in evaluations),
        mismatched_cases=sum(
            item.verdict is BenchmarkCaseVerdict.MISMATCHED for item in evaluations
        ),
        not_evaluated_cases=sum(
            item.verdict is BenchmarkCaseVerdict.NOT_EVALUATED for item in evaluations
        ),
        completion_matches=sum(
            item.completion.status is BenchmarkDimensionStatus.MATCH for item in evaluations
        ),
        evidence_matches=sum(
            item.evidence.status is BenchmarkDimensionStatus.MATCH for item in evaluations
        ),
        code_delta_matches=sum(
            item.code_delta.status is BenchmarkDimensionStatus.MATCH for item in evaluations
        ),
        reliability_matches=sum(
            item.reliability.status is BenchmarkDimensionStatus.MATCH for item in evaluations
        ),
        latency_matches=sum(
            item.latency.status is BenchmarkDimensionStatus.MATCH for item in evaluations
        ),
        cost_data=BenchmarkDataAvailability.NOT_AVAILABLE,
    )
    payload = {
        "schema_version": 1,
        "suite_id": suite.suite_id,
        "suite_version": suite.suite_version,
        "suite_sha256": suite_sha256,
        "execution": bundle.execution.model_dump(mode="json"),
        "cases": [item.model_dump(mode="json") for item in evaluations],
        "summary": summary.model_dump(mode="json"),
    }
    return BenchmarkReport(
        **payload,
        report_sha256=canonical_sha256(payload),
    )
