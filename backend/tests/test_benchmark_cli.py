from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from app.api.models import DispatchStatus
from app.benchmark.cli import main
from app.benchmark.io import canonical_sha256, write_model
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkChangedFilesMode,
    BenchmarkDiffObservation,
    BenchmarkEvidenceObservation,
    BenchmarkExecutionConfig,
    BenchmarkExpectations,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkRuntimeEventObservation,
    BenchmarkSuite,
)
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


def _suite() -> BenchmarkSuite:
    return BenchmarkSuite(
        suite_id="cli-suite",
        suite_version="1.0.0",
        description="cli suite",
        cases=(
            BenchmarkCase(
                case_id="cli-case",
                description="cli case",
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
                    max_retries=1,
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
            ),
        ),
    )


def _write_suite(path: Path, suite: BenchmarkSuite) -> str:
    path.write_text(
        json.dumps(suite.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return canonical_sha256(suite)


def _bundle(suite: BenchmarkSuite, suite_sha: str) -> BenchmarkObservationBundle:
    return BenchmarkObservationBundle(
        suite_id=suite.suite_id,
        suite_version=suite.suite_version,
        suite_sha256=suite_sha,
        execution=BenchmarkExecutionConfig(),
        observations=(
            BenchmarkObservation(
                suite_id=suite.suite_id,
                suite_version=suite.suite_version,
                suite_sha256=suite_sha,
                case_id="cli-case",
                state=BenchmarkObservationState.TERMINAL,
                project_id=UUID("11111111-1111-1111-1111-111111111111"),
                run_id=UUID("22222222-2222-2222-2222-222222222222"),
                dispatch_status=DispatchStatus.QUEUED,
                observed_base_commit="a" * 40,
                run_status=PersistedRunStatus.SUCCEEDED,
                terminal_duration_ms=1000,
                evidence_kinds=(
                    PersistenceEvidenceKind.REVIEW_DECISION,
                    PersistenceEvidenceKind.VERIFICATION_RESULT,
                    PersistenceEvidenceKind.WORKER_EXECUTION,
                ),
                evidence=BenchmarkEvidenceObservation(
                    total_records=6,
                    developer_runs=1,
                    verification_attempts=1,
                    review_decisions=1,
                    repair_attempts=0,
                    failure_reports=0,
                    dispatch_events=1,
                    worker_executions=1,
                    merge_queue_snapshots=0,
                    merge_conflicts=0,
                    integration_gate_evaluations=0,
                    human_decisions=0,
                ),
                runtime_events=BenchmarkRuntimeEventObservation(
                    total_events=7,
                    warning_events=0,
                    error_events=0,
                    lease_acquisitions=1,
                    lease_takeovers=0,
                    lease_releases=1,
                    latest_sequence=7,
                ),
                diff=BenchmarkDiffObservation(
                    source_evidence_sha256="f" * 64,
                    base_commit="a" * 40,
                    head_commit="b" * 40,
                    changed_file_count=1,
                    changed_files=("result.txt",),
                    additions=1,
                    deletions=0,
                    truncated=False,
                    omitted_file_count=0,
                ),
            ),
        ),
    )


def test_validate_and_evaluate_cli_produce_reproducible_report(
    tmp_path: Path,
    capsys,
) -> None:
    suite = _suite()
    suite_path = tmp_path / "suite.json"
    suite_sha = _write_suite(suite_path, suite)

    assert main(["validate", "--suite", str(suite_path)]) == 0
    validate_output = json.loads(capsys.readouterr().out)
    assert validate_output["suite_sha256"] == suite_sha

    observations_path = tmp_path / "observations.json"
    write_model(observations_path, _bundle(suite, suite_sha))

    output = tmp_path / "report"
    assert (
        main(
            [
                "evaluate",
                "--suite",
                str(suite_path),
                "--observations",
                str(observations_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    first = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert first["summary"]["matched_cases"] == 1
    assert "score" not in first
    assert "Benchmark verdicts are read-only comparisons" in (output / "report.md").read_text(
        encoding="utf-8"
    )

    assert (
        main(
            [
                "evaluate",
                "--suite",
                str(suite_path),
                "--observations",
                str(observations_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    second = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert second["report_sha256"] == first["report_sha256"]
