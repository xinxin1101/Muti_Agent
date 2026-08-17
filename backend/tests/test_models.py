import pytest
from pydantic import ValidationError

from app.models import (
    AgentRequest,
    AgentResponse,
    CheckResult,
    FailureReport,
    ReviewDecision,
    TaskContract,
    VerificationResult,
)


def make_task_contract(**overrides):
    payload = {
        "task_id": "TASK-001",
        "objective": "Implement a calculator divide function",
        "readable_files": ["src/**", "tests/**"],
        "writable_files": ["src/**"],
        "readonly_files": ["tests/**"],
        "acceptance_criteria": [
            "divide(6, 2) returns 3",
            "division by zero raises ZeroDivisionError",
        ],
        "verification_commands": ["pytest -q", "ruff check ."],
        "max_retries": 2,
    }
    payload.update(overrides)
    return TaskContract.model_validate(payload)


def test_task_contract_accepts_valid_scope() -> None:
    contract = make_task_contract()

    assert contract.task_id == "TASK-001"
    assert contract.writable_files == ["src/**"]
    assert contract.readonly_files == ["tests/**"]
    assert contract.max_retries == 2


@pytest.mark.parametrize(
    "pattern",
    [
        "/etc/passwd",
        "../outside.py",
        "src/../../outside.py",
        r"src\module.py",
        "C:/workspace/file.py",
        ".",
        "   ",
    ],
)
def test_task_contract_rejects_unsafe_scope_patterns(pattern: str) -> None:
    with pytest.raises(ValidationError):
        make_task_contract(writable_files=[pattern])


def test_task_contract_rejects_writable_readonly_overlap() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        make_task_contract(
            writable_files=["tests/**"],
            readonly_files=["tests/**"],
        )


def test_task_contract_rejects_duplicate_scope_patterns() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        make_task_contract(writable_files=["src/**", "src/**"])


def test_task_contract_rejects_empty_acceptance_criterion() -> None:
    with pytest.raises(ValidationError):
        make_task_contract(acceptance_criteria=["   "])


def test_agent_request_and_response_are_provider_neutral() -> None:
    request = AgentRequest.model_validate(
        {
            "role": "planner",
            "model": "provider/model-a",
            "messages": [
                {"role": "system", "content": "Return a task contract."},
                {"role": "user", "content": "Implement division."},
            ],
            "temperature": 0.2,
        }
    )
    response = AgentResponse.model_validate(
        {
            "model": "provider/model-a",
            "content": "{}",
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 4,
                "total_tokens": 16,
            },
            "latency_ms": 85,
            "finish_reason": "stop",
        }
    )

    assert request.role == "planner"
    assert request.messages[0].role == "system"
    assert response.usage.total_tokens == 16


def test_agent_request_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AgentRequest.model_validate(
            {
                "role": "developer",
                "model": "provider/model-b",
                "messages": [{"role": "user", "content": "Implement the task."}],
                "unknown": True,
            }
        )


def test_review_pass_cannot_contain_issues() -> None:
    with pytest.raises(ValidationError, match="must not contain issues"):
        ReviewDecision.model_validate(
            {
                "decision": "PASS",
                "summary": "Implementation is acceptable.",
                "issues": [
                    {
                        "severity": "high",
                        "message": "A blocking issue exists.",
                    }
                ],
            }
        )


def test_changes_requested_requires_issue_evidence() -> None:
    with pytest.raises(ValidationError, match="require at least one issue"):
        ReviewDecision.model_validate(
            {
                "decision": "CHANGES_REQUESTED",
                "summary": "Changes are required.",
                "issues": [],
            }
        )


def test_verification_result_requires_consistent_aggregate_status() -> None:
    failed_check = CheckResult.model_validate(
        {
            "check_type": "test",
            "name": "pytest",
            "command": "pytest -q",
            "passed": False,
            "exit_code": 1,
            "stderr": "1 failed",
            "failure_type": "TEST_FAILURE",
        }
    )

    with pytest.raises(ValidationError, match="must match all check results"):
        VerificationResult(passed=True, checks=[failed_check])


def test_failed_check_requires_failure_type() -> None:
    with pytest.raises(ValidationError, match="require a failure_type"):
        CheckResult.model_validate(
            {
                "check_type": "lint",
                "name": "ruff",
                "command": "ruff check .",
                "passed": False,
                "exit_code": 1,
            }
        )


def test_failure_report_accepts_taxonomy_value() -> None:
    report = FailureReport.model_validate(
        {
            "failure_type": "SCOPE_VIOLATION",
            "source": "runtime",
            "message": "Developer changed a read-only test file.",
            "retryable": False,
            "evidence": ["tests/test_calculator.py"],
        }
    )

    assert report.failure_type == "SCOPE_VIOLATION"
    assert report.retryable is False
