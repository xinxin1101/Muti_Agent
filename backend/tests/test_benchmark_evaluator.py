from __future__ import annotations

from uuid import UUID

import pytest

from app.api.models import DispatchStatus
from app.benchmark.evaluator import evaluate_suite
from app.benchmark.io import canonical_sha256
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkCaseVerdict,
    BenchmarkChangedFilesMode,
    BenchmarkDiffObservation,
    BenchmarkDimensionStatus,
    BenchmarkEvidenceObservation,
    BenchmarkExecutionConfig,
    BenchmarkExpectations,
    BenchmarkFailure,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkRuntimeEventObservation,
    BenchmarkSuite,
)
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


def _case() -> BenchmarkCase:
    return BenchmarkCase(
        case_id="case",
        description="case",
        repository_url="https://github.com/example/repo",
        default_branch="benchmark-v1",
        expected_base_commit="a" * 40,
        task=TaskContract(
            task_id="task",
            objective="Create result.txt.",
            readable_files=[],
            writable_files=["result.txt"],
            readonly_files=[],
            acceptance_criteria=["result.txt exists."],
            verification_commands=[
                "python -c \"from pathlib import Path; assert Path('result.txt').exists()\""
            ],
            max_retries=2,
        ),
        expectations=BenchmarkExpectations(
            terminal_status=PersistedRunStatus.SUCCEEDED,
            required_evidence_kinds=(
                PersistenceEvidenceKind.VERIFICATION_RESULT,
                PersistenceEvidenceKind.REVIEW_DECISION,
                PersistenceEvidenceKind.WORKER_EXECUTION,
            ),
            changed_files=("result.txt",),
            changed_files_mode=BenchmarkChangedFilesMode.EXACT,
            max_terminal_duration_ms=5000,
            max_repair_attempts=1,
            max_human_decisions=0,
        ),
    )


def _suite() -> tuple[BenchmarkSuite, str]:
    suite = BenchmarkSuite(
        suite_id="suite",
        suite_version="1.0.0",
        description="suite",
        cases=(_case(),),
    )
    return suite, canonical_sha256(suite)


def _evidence(*, repairs: int = 0, human: int = 0) -> BenchmarkEvidenceObservation:
    return BenchmarkEvidenceObservation(
        total_records=7,
        developer_runs=1,
        verification_attempts=1,
        review_decisions=1,
        repair_attempts=repairs,
        failure_reports=0,
        dispatch_events=1,
        worker_executions=1,
        merge_queue_snapshots=0,
        merge_conflicts=0,
        integration_gate_evaluations=0,
        human_decisions=human,
    )


def _events() -> BenchmarkRuntimeEventObservation:
    return BenchmarkRuntimeEventObservation(
        total_events=8,
        warning_events=0,
        error_events=0,
        lease_acquisitions=1,
        lease_takeovers=0,
        lease_releases=1,
        latest_sequence=8,
    )


def _terminal_observation(
    suite: BenchmarkSuite,
    suite_sha: str,
    *,
    status: PersistedRunStatus = PersistedRunStatus.SUCCEEDED,
    evidence_kinds: tuple[PersistenceEvidenceKind, ...] | None = None,
    changed_files: tuple[str, ...] = ("result.txt",),
    duration_ms: int = 2500,
    repairs: int = 0,
    human: int = 0,
    diff_complete: bool = True,
) -> BenchmarkObservation:
    return BenchmarkObservation(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        case_id="case",
        state=BenchmarkObservationState.TERMINAL,
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        dispatch_status=DispatchStatus.QUEUED,
        observed_base_commit="a" * 40,
        run_status=status,
        terminal_duration_ms=duration_ms,
        evidence_kinds=(
            evidence_kinds
            if evidence_kinds is not None
            else (
                PersistenceEvidenceKind.REVIEW_DECISION,
                PersistenceEvidenceKind.VERIFICATION_RESULT,
                PersistenceEvidenceKind.WORKER_EXECUTION,
            )
        ),
        evidence=_evidence(repairs=repairs, human=human),
        runtime_events=_events(),
        diff=BenchmarkDiffObservation(
            source_evidence_sha256="b" * 64,
            base_commit="a" * 40,
            head_commit="c" * 40,
            changed_file_count=(len(changed_files) if diff_complete else len(changed_files) + 1),
            changed_files=changed_files,
            additions=1,
            deletions=0,
            truncated=not diff_complete,
            omitted_file_count=0 if diff_complete else 1,
        ),
    )


def _bundle(
    suite: BenchmarkSuite,
    suite_sha: str,
    observation: BenchmarkObservation,
) -> BenchmarkObservationBundle:
    return BenchmarkObservationBundle(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        execution=BenchmarkExecutionConfig(),
        observations=(observation,),
    )


def test_evaluator_matches_each_dimension_without_creating_a_total_score() -> None:
    suite, suite_sha = _suite()
    observation = _terminal_observation(suite, suite_sha)

    report = evaluate_suite(suite, suite_sha, _bundle(suite, suite_sha, observation))

    item = report.cases[0]
    assert item.verdict is BenchmarkCaseVerdict.MATCHED
    assert item.completion.status is BenchmarkDimensionStatus.MATCH
    assert item.evidence.status is BenchmarkDimensionStatus.MATCH
    assert item.code_delta.status is BenchmarkDimensionStatus.MATCH
    assert item.reliability.status is BenchmarkDimensionStatus.MATCH
    assert item.latency.status is BenchmarkDimensionStatus.MATCH
    assert report.summary.matched_cases == 1
    assert "score" not in report.model_dump(mode="json")
    assert (
        report.report_sha256
        == evaluate_suite(
            suite,
            suite_sha,
            _bundle(suite, suite_sha, observation),
        ).report_sha256
    )


def test_evaluator_reports_independent_mismatches_without_rewriting_runtime_status() -> None:
    suite, suite_sha = _suite()
    observation = _terminal_observation(
        suite,
        suite_sha,
        status=PersistedRunStatus.FAILED,
        evidence_kinds=(PersistenceEvidenceKind.VERIFICATION_RESULT,),
        changed_files=("unexpected.txt",),
        duration_ms=7000,
        repairs=2,
        human=1,
    )

    report = evaluate_suite(suite, suite_sha, _bundle(suite, suite_sha, observation))

    item = report.cases[0]
    assert item.runtime_status is PersistedRunStatus.FAILED
    assert item.verdict is BenchmarkCaseVerdict.MISMATCHED
    assert item.completion.status is BenchmarkDimensionStatus.MISMATCH
    assert item.evidence.status is BenchmarkDimensionStatus.MISMATCH
    assert item.code_delta.status is BenchmarkDimensionStatus.MISMATCH
    assert item.reliability.status is BenchmarkDimensionStatus.MISMATCH
    assert item.latency.status is BenchmarkDimensionStatus.MISMATCH
    assert {
        "TERMINAL_STATUS_MISMATCH",
        "EVIDENCE_REQUIREMENT_MISMATCH",
        "CODE_DELTA_MISMATCH",
        "RELIABILITY_BUDGET_MISMATCH",
        "LATENCY_BUDGET_MISMATCH",
    }.issubset(set(item.failure_modes))


def test_fixture_drift_is_not_evaluated_instead_of_becoming_a_failure_score() -> None:
    suite, suite_sha = _suite()
    observation = BenchmarkObservation(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        case_id="case",
        state=BenchmarkObservationState.FIXTURE_DRIFT,
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        dispatch_status=DispatchStatus.QUEUED,
        observed_base_commit="d" * 40,
        failure=BenchmarkFailure(
            code="FIXTURE_DRIFT",
            message="fixture moved",
        ),
    )

    report = evaluate_suite(suite, suite_sha, _bundle(suite, suite_sha, observation))

    item = report.cases[0]
    assert item.verdict is BenchmarkCaseVerdict.NOT_EVALUATED
    assert item.completion.status is BenchmarkDimensionStatus.NOT_EVALUATED
    assert item.code_delta.status is BenchmarkDimensionStatus.NOT_EVALUATED
    assert report.summary.not_evaluated_cases == 1


def test_incomplete_bounded_diff_prevents_code_delta_claim() -> None:
    suite, suite_sha = _suite()
    observation = _terminal_observation(
        suite,
        suite_sha,
        diff_complete=False,
    )

    report = evaluate_suite(suite, suite_sha, _bundle(suite, suite_sha, observation))

    item = report.cases[0]
    assert item.code_delta.status is BenchmarkDimensionStatus.NOT_EVALUATED
    assert item.verdict is BenchmarkCaseVerdict.NOT_EVALUATED
    assert "DIFF_INCOMPLETE" in item.failure_modes


def test_evaluator_fails_closed_on_wrong_suite_identity() -> None:
    suite, suite_sha = _suite()
    observation = _terminal_observation(suite, suite_sha)
    bundle = _bundle(suite, suite_sha, observation).model_copy(update={"suite_sha256": "f" * 64})

    with pytest.raises(ValueError, match="do not belong"):
        evaluate_suite(suite, suite_sha, bundle)
