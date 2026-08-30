import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app import agents, models
from app.runtime import FailureClassifier
from app.verification import DeterministicVerifier, LocalProcessVerificationRunner
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeDriver received more calls than expected")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/repair",
        content=content,
        tool_calls=tool_calls or [],
        latency_ms=4,
        finish_reason="stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path, *, value: int = 1) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "module.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
    (root / "tests" / "test_value.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_value_is_two() -> None:\n"
        "    assert 'VALUE = 2' in Path('module.py').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task(*, max_retries: int = 2) -> models.TaskContract:
    return models.TaskContract(
        task_id="REPAIR-001",
        objective="Make module VALUE equal to 2 without modifying tests.",
        readable_files=["module.py", "tests/**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["The value test passes."],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=max_retries,
    )


def _test_failure() -> models.FailureReport:
    return models.FailureReport(
        failure_type=models.FailureType.TEST_FAILURE,
        source=models.FailureSource.VERIFICATION,
        message="Deterministic pytest verification failed.",
        retryable=True,
        evidence=["check=pytest", "stderr=expected VALUE = 2"],
    )


def test_failure_classifier_preserves_verification_evidence() -> None:
    result = models.VerificationResult(
        passed=False,
        checks=[
            models.CheckResult(
                check_type=models.CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=False,
                exit_code=1,
                stderr="assertion failed",
                failure_type=models.FailureType.TEST_FAILURE,
            )
        ],
    )

    reports = FailureClassifier.from_verification(result)

    assert len(reports) == 1
    assert reports[0].failure_type is models.FailureType.TEST_FAILURE
    assert reports[0].retryable is True
    assert "stderr=assertion failed" in reports[0].evidence
    assert FailureClassifier.repairable(reports) == reports


def test_failure_classifier_turns_review_issues_into_targeted_evidence() -> None:
    decision = models.ReviewDecision(
        decision=models.ReviewOutcome.CHANGES_REQUESTED,
        summary="Authentication semantics are unsafe.",
        issues=[
            models.ReviewIssue(
                severity=models.ReviewSeverity.HIGH,
                message="verify_token always returns true",
                file="auth.py",
                line=2,
            )
        ],
    )

    reports = FailureClassifier.from_review(decision)

    assert len(reports) == 1
    report = reports[0]
    assert report.failure_type is models.FailureType.REVIEW_REJECTED
    assert report.source is models.FailureSource.REVIEW
    assert report.retryable is True
    assert any("auth.py:2" in item for item in report.evidence)
    assert any("always returns true" in item for item in report.evidence)


def test_failure_classifier_does_not_make_scope_failure_repairable() -> None:
    report = models.FailureReport(
        failure_type=models.FailureType.SCOPE_VIOLATION,
        source=models.FailureSource.VERIFICATION,
        message="Scope failed.",
        retryable=False,
        evidence=["tests/test_value.py was modified"],
    )

    assert FailureClassifier.repairable([report]) == []


def test_repair_agent_uses_repair_role_targeted_evidence_and_same_tools(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path, value=2)
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    failure = _test_failure()
    driver = FakeDriver([_response(content="Applied the targeted value repair.")])
    agent = agents.RepairAgent(driver=driver, model="test/repair")

    result = asyncio.run(
        agent.repair(
            _task(),
            [failure],
            attempt=1,
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.attempt == 1
    assert result.failure_types == [models.FailureType.TEST_FAILURE]
    assert result.stop_reason is models.RepairStopReason.MODEL_STOP
    request = driver.requests[0]
    assert request.role is models.AgentRole.REPAIR
    assert {tool.name for tool in request.tools} == {
        "list_files",
        "read_file",
        "read_files",
        "search_code",
        "search_code_many",
        "write_file",
        "apply_patch",
    }
    assert "stderr=expected VALUE = 2" in request.messages[1].content
    assert "attempt 1 of 2" in request.messages[1].content
    assert "not a success verdict" in request.messages[0].content
    assert request.max_output_tokens == 1_000
    assert "Prefer tool calls over prose" in request.messages[0].content
    assert "exactly three concise items" in request.messages[0].content


def test_first_verification_failure_is_targeted_repaired_then_passes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    task = _task()
    # This unit test exercises repair classification and tool application, not Docker sandbox
    # capability.  Use the explicit local runner so its result is independent of the host's
    # Docker Desktop availability.
    verifier = DeterministicVerifier(
        command_timeout_seconds=10,
        command_runner=LocalProcessVerificationRunner(),
    )

    first = verifier.verify(task, workspace=workspace)
    failures = FailureClassifier.repairable(FailureClassifier.from_verification(first))

    assert first.passed is False
    assert [failure.failure_type for failure in failures] == [models.FailureType.TEST_FAILURE]

    patch_call = models.ToolCall(
        id="repair-1",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
            }
        ),
    )
    driver = FakeDriver(
        [
            _response(tool_calls=[patch_call]),
            _response(content="Changed only module.py to address the failing assertion."),
        ]
    )
    repair = agents.RepairAgent(driver=driver, model="test/repair")

    repair_result = asyncio.run(repair.repair(task, failures, attempt=1, workspace=workspace))
    second = verifier.verify(task, workspace=workspace)

    assert repair_result.stop_reason is models.RepairStopReason.MODEL_STOP
    assert repair_result.tool_calls == 1
    assert repair_result.changed_files == ["module.py"]
    assert second.passed is True
    assert (root / "module.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert "Targeted FailureReport evidence" in driver.requests[0].messages[1].content


def test_repair_agent_rejects_nonrepairable_failure_before_model_call(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    failure = models.FailureReport(
        failure_type=models.FailureType.SCOPE_VIOLATION,
        source=models.FailureSource.VERIFICATION,
        message="Protected tests changed.",
        retryable=False,
        evidence=["tests/test_value.py"],
    )
    driver = FakeDriver([])
    repair = agents.RepairAgent(driver=driver, model="test/repair")

    with pytest.raises(ValueError, match="repair accepts only retryable"):
        asyncio.run(
            repair.repair(
                _task(),
                [failure],
                attempt=1,
                workspace=LocalGitWorkspace(root),
            )
        )

    assert driver.requests == []


def test_repair_budget_exhaustion_is_terminal_and_preserves_root_failure(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = FakeDriver([])
    repair = agents.RepairAgent(driver=driver, model="test/repair")

    with pytest.raises(agents.RepairBudgetExhaustedError) as exc_info:
        asyncio.run(
            repair.repair(
                _task(max_retries=1),
                [_test_failure()],
                attempt=2,
                workspace=LocalGitWorkspace(root),
            )
        )

    terminal = exc_info.value.failures[0]
    assert terminal.failure_type is models.FailureType.TEST_FAILURE
    assert terminal.retryable is False
    assert "Repair retry budget was exhausted" in terminal.message
    assert "repair_attempts_exhausted=1" in terminal.evidence
    assert driver.requests == []


def test_repair_agent_requires_nonempty_failure_evidence(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    driver = FakeDriver([])
    repair = agents.RepairAgent(driver=driver, model="test/repair")

    with pytest.raises(ValueError, match="at least one failure"):
        asyncio.run(
            repair.repair(
                _task(),
                [],
                attempt=1,
                workspace=LocalGitWorkspace(root),
            )
        )

    assert driver.requests == []


def test_review_pass_produces_no_repair_failure() -> None:
    decision = models.ReviewDecision(
        decision=models.ReviewOutcome.PASS,
        summary="Verified diff is semantically acceptable.",
        issues=[],
    )

    assert FailureClassifier.from_review(decision) == []
