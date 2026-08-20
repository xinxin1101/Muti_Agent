from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.benchmark.io import canonical_sha256, load_suite
from app.benchmark.models import (
    BenchmarkExecutionConfig,
    BenchmarkExpectations,
    BenchmarkSuite,
)
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


def _task() -> TaskContract:
    return TaskContract(
        task_id="case-task",
        objective="Create result.txt with one marker.",
        readable_files=[],
        writable_files=["result.txt"],
        readonly_files=[],
        acceptance_criteria=["result.txt contains ok."],
        verification_commands=[
            "python -c \"from pathlib import Path; assert Path('result.txt').exists()\""
        ],
        max_retries=1,
    )


def _suite_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "suite_id": "suite",
        "suite_version": "1.0.0",
        "description": "test suite",
        "cases": [
            {
                "case_id": "case",
                "description": "case",
                "repository_url": "https://github.com/example/repo",
                "default_branch": "benchmark-v1",
                "expected_base_commit": "a" * 40,
                "task": _task().model_dump(mode="json"),
                "expectations": {
                    "terminal_status": "SUCCEEDED",
                    "required_evidence_kinds": ["VERIFICATION_RESULT"],
                    "changed_files": ["result.txt"],
                    "changed_files_mode": "EXACT",
                    "max_terminal_duration_ms": 1000,
                    "max_repair_attempts": 1,
                    "max_human_decisions": 0,
                },
                "tags": ["unit"],
            }
        ],
    }


def test_suite_hash_is_semantic_and_deterministic(tmp_path: Path) -> None:
    payload = _suite_payload()
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    second.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    suite_a, sha_a = load_suite(first)
    suite_b, sha_b = load_suite(second)

    assert suite_a == suite_b
    assert sha_a == sha_b == canonical_sha256(suite_a)


@pytest.mark.parametrize(
    "repository_url",
    [
        "http://github.com/example/repo",
        "https://user:secret@github.com/example/repo",
        "https://example.com/example/repo",
        "https://github.com/example/repo?token=secret",
    ],
)
def test_suite_rejects_untrusted_repository_urls(repository_url: str) -> None:
    payload = _suite_payload()
    payload["cases"][0]["repository_url"] = repository_url  # type: ignore[index]

    with pytest.raises(ValidationError):
        BenchmarkSuite.model_validate(payload)


def test_expectations_reject_running_ground_truth() -> None:
    with pytest.raises(ValidationError):
        BenchmarkExpectations(
            terminal_status=PersistedRunStatus.RUNNING,
            required_evidence_kinds=(PersistenceEvidenceKind.VERIFICATION_RESULT,),
            changed_files=("result.txt",),
        )


@pytest.mark.parametrize(
    "api_base_url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com/api?token=x",
    ],
)
def test_execution_config_rejects_credential_or_plaintext_remote_origins(
    api_base_url: str,
) -> None:
    with pytest.raises(ValidationError):
        BenchmarkExecutionConfig(api_base_url=api_base_url)


def test_execution_config_allows_loopback_http_and_remote_https() -> None:
    assert (
        BenchmarkExecutionConfig(api_base_url="http://127.0.0.1:8000").api_base_url
        == "http://127.0.0.1:8000"
    )
    assert (
        BenchmarkExecutionConfig(api_base_url="https://devflow.example.com").api_base_url
        == "https://devflow.example.com"
    )


def test_suite_rejects_duplicate_case_ids() -> None:
    payload = _suite_payload()
    payload["cases"] = [payload["cases"][0], payload["cases"][0]]  # type: ignore[index]

    with pytest.raises(ValidationError):
        BenchmarkSuite.model_validate(payload)
