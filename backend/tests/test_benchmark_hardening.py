from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.models import DispatchStatus
from app.benchmark.evaluator import evaluate_suite
from app.benchmark.io import canonical_sha256
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkChangedFilesMode,
    BenchmarkDataAvailability,
    BenchmarkDemoManifest,
    BenchmarkDiffObservation,
    BenchmarkEvidenceObservation,
    BenchmarkExecutionConfig,
    BenchmarkExpectations,
    BenchmarkExperimentIdentityBasis,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkRuntimeEventObservation,
    BenchmarkSuite,
)
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind

PROJECT = UUID("11111111-1111-1111-1111-111111111111")


def _task(case_id: str) -> TaskContract:
    return TaskContract(
        task_id=case_id,
        objective=f"Create {case_id}.txt.",
        readable_files=[],
        writable_files=[f"{case_id}.txt"],
        readonly_files=[],
        acceptance_criteria=[f"{case_id}.txt exists."],
        verification_commands=["python -c \"print('ok')\""],
        max_retries=2,
    )


def _case(case_id: str, status: PersistedRunStatus) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        description=case_id,
        repository_url="https://github.com/example/repo",
        default_branch="benchmark-v1",
        expected_base_commit="a" * 40,
        task=_task(case_id),
        expectations=BenchmarkExpectations(
            terminal_status=status,
            required_evidence_kinds=(PersistenceEvidenceKind.VERIFICATION_RESULT,),
            changed_files=(f"{case_id}.txt",),
            changed_files_mode=BenchmarkChangedFilesMode.EXACT,
            max_terminal_duration_ms=10_000,
            max_repair_attempts=2,
            max_human_decisions=0,
        ),
    )


def _evidence(
    *,
    repairs: int,
    reviews: int,
    rejections: int,
    scope_violations: int,
    prompt_tokens: int,
    completion_tokens: int,
) -> BenchmarkEvidenceObservation:
    return BenchmarkEvidenceObservation(
        total_records=5,
        developer_runs=1,
        verification_attempts=repairs + 1,
        review_decisions=reviews,
        reviewer_rejections=rejections,
        repair_attempts=repairs,
        failure_reports=scope_violations,
        scope_violations=scope_violations,
        dispatch_events=1,
        worker_executions=1,
        merge_queue_snapshots=0,
        merge_conflicts=0,
        integration_gate_evaluations=0,
        human_decisions=0,
        developer_prompt_tokens=prompt_tokens,
        developer_completion_tokens=completion_tokens,
        developer_total_tokens=prompt_tokens + completion_tokens,
        repair_prompt_tokens=0,
        repair_completion_tokens=0,
        repair_total_tokens=0,
        reviewer_token_usage_available=False,
        estimated_cost_available=False,
    )


def _observation(
    suite: BenchmarkSuite,
    suite_sha: str,
    case_id: str,
    status: PersistedRunStatus,
    duration: int,
    evidence: BenchmarkEvidenceObservation,
) -> BenchmarkObservation:
    return BenchmarkObservation(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        case_id=case_id,
        state=BenchmarkObservationState.TERMINAL,
        project_id=PROJECT,
        run_id=UUID(f"22222222-2222-2222-2222-{int(case_id[-1]):012d}"),
        dispatch_status=DispatchStatus.QUEUED,
        observed_base_commit="a" * 40,
        run_status=status,
        terminal_duration_ms=duration,
        evidence_kinds=(PersistenceEvidenceKind.VERIFICATION_RESULT,),
        evidence=evidence,
        runtime_events=BenchmarkRuntimeEventObservation(
            total_events=1,
            warning_events=0,
            error_events=0,
            lease_acquisitions=0,
            lease_takeovers=0,
            lease_releases=0,
            latest_sequence=1,
        ),
        diff=BenchmarkDiffObservation(
            source_evidence_sha256="f" * 64,
            base_commit="a" * 40,
            head_commit="b" * 40,
            changed_file_count=1,
            changed_files=(f"{case_id}.txt",),
            additions=1,
            deletions=0,
            truncated=False,
            omitted_file_count=0,
        ),
    )


def test_aggregate_statistics_measure_terminal_observations_without_becoming_a_score() -> None:
    suite = BenchmarkSuite(
        suite_id="aggregate-suite",
        suite_version="1.0.0",
        description="aggregate suite",
        cases=(
            _case("case1", PersistedRunStatus.SUCCEEDED),
            _case("case2", PersistedRunStatus.SUCCEEDED),
            _case("case3", PersistedRunStatus.FAILED),
        ),
    )
    suite_sha = canonical_sha256(suite)
    bundle = BenchmarkObservationBundle(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        execution=BenchmarkExecutionConfig(),
        observations=(
            _observation(
                suite,
                suite_sha,
                "case1",
                PersistedRunStatus.SUCCEEDED,
                1000,
                _evidence(
                    repairs=0,
                    reviews=1,
                    rejections=0,
                    scope_violations=0,
                    prompt_tokens=10,
                    completion_tokens=5,
                ),
            ),
            _observation(
                suite,
                suite_sha,
                "case2",
                PersistedRunStatus.SUCCEEDED,
                2000,
                _evidence(
                    repairs=1,
                    reviews=2,
                    rejections=1,
                    scope_violations=0,
                    prompt_tokens=20,
                    completion_tokens=10,
                ),
            ),
            _observation(
                suite,
                suite_sha,
                "case3",
                PersistedRunStatus.FAILED,
                3000,
                _evidence(
                    repairs=0,
                    reviews=0,
                    rejections=0,
                    scope_violations=1,
                    prompt_tokens=5,
                    completion_tokens=2,
                ),
            ),
        ),
    )

    report = evaluate_suite(suite, suite_sha, bundle)
    aggregate = report.summary.aggregates

    assert aggregate.terminal_cases == 3
    assert aggregate.successful_cases == 2
    assert aggregate.task_success_rate == pytest.approx(2 / 3)
    assert aggregate.first_pass_successes == 1
    assert aggregate.first_pass_success_rate == pytest.approx(1 / 3)
    assert aggregate.repaired_successes == 1
    assert aggregate.repaired_success_rate == pytest.approx(1 / 3)
    assert aggregate.average_retry_count == pytest.approx(1 / 3)
    assert aggregate.review_decisions == 3
    assert aggregate.reviewer_rejections == 1
    assert aggregate.reviewer_rejection_rate == pytest.approx(1 / 3)
    assert aggregate.scope_violations_detected == 1
    assert aggregate.mean_terminal_duration_ms == 2000
    assert aggregate.median_terminal_duration_ms == 2000
    assert aggregate.prompt_tokens_observed == 35
    assert aggregate.completion_tokens_observed == 17
    assert aggregate.total_tokens_observed == 52
    assert aggregate.token_usage is BenchmarkDataAvailability.PARTIAL
    assert aggregate.cost_data is BenchmarkDataAvailability.NOT_AVAILABLE
    serialized = report.model_dump_json()
    assert "health_score" not in serialized
    assert "weighted_score" not in serialized


def test_experiment_identity_is_all_or_nothing_and_explicitly_operator_declared() -> None:
    with pytest.raises(ValidationError):
        BenchmarkExecutionConfig(
            identity_basis=BenchmarkExperimentIdentityBasis.OPERATOR_DECLARED,
            runtime_commit="a" * 40,
        )

    config = BenchmarkExecutionConfig(
        identity_basis=BenchmarkExperimentIdentityBasis.OPERATOR_DECLARED,
        runtime_commit="a" * 40,
        provider="siliconflow",
        developer_model="developer-model",
        reviewer_model="reviewer-model",
        repair_model="repair-model",
        context_strategy="context-packet",
        verifier_identity="devflow-verifier:py311",
    )
    assert config.runtime_commit == "a" * 40
    assert config.identity_basis is BenchmarkExperimentIdentityBasis.OPERATOR_DECLARED


def test_control_plane_manifest_requires_exactly_the_five_v1_scenario_kinds() -> None:
    payload = {
        "schema_version": 1,
        "manifest_id": "demo",
        "manifest_version": "1.0.0",
        "description": "demo",
        "scenarios": [
            {
                "kind": "NORMAL_SUCCESS",
                "description": "normal",
                "pytest_nodeid": "tests/test_a.py::test_a",
            }
        ],
    }

    with pytest.raises(ValidationError):
        BenchmarkDemoManifest.model_validate(payload)
