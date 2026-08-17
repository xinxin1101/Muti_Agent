import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app import agents, models
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeDriver received more calls than expected")
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(content: str) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/reviewer",
        content=content,
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


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return token == 'valid'\n",
        encoding="utf-8",
    )
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="AUTH-REVIEW-001",
        objective="Keep token verification secure while refactoring authentication.",
        readable_files=["**"],
        writable_files=["auth.py", "helpers.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Invalid tokens must be rejected."],
        verification_commands=["pytest -q", "ruff check ."],
    )


def _passed_verification() -> models.VerificationResult:
    return models.VerificationResult(
        passed=True,
        checks=[
            models.CheckResult(
                check_type=models.CheckType.SCOPE,
                name="git_scope",
                passed=True,
            ),
            models.CheckResult(
                check_type=models.CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=True,
                exit_code=0,
            ),
            models.CheckResult(
                check_type=models.CheckType.LINT,
                name="ruff",
                command="ruff check .",
                passed=True,
                exit_code=0,
            ),
        ],
    )


def _failed_verification() -> models.VerificationResult:
    return models.VerificationResult(
        passed=False,
        checks=[
            models.CheckResult(
                check_type=models.CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=False,
                exit_code=1,
                stderr="one test failed",
                failure_type=models.FailureType.TEST_FAILURE,
            )
        ],
    )


def test_reviewer_runs_only_after_hard_gate_passes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    driver = FakeDriver([])
    reviewer = agents.ReviewerAgent(driver=driver, model="test/reviewer")

    with pytest.raises(ValueError, match="passed deterministic VerificationResult"):
        asyncio.run(
            reviewer.review(
                _task(),
                _failed_verification(),
                workspace=LocalGitWorkspace(root),
            )
        )

    assert driver.requests == []


def test_reviewer_receives_task_actual_diff_and_verification_without_tools(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return token.startswith('signed:')\n",
        encoding="utf-8",
    )
    driver = FakeDriver(
        [
            _response(
                json.dumps(
                    {
                        "decision": "PASS",
                        "summary": "The refactor preserves token rejection semantics.",
                        "issues": [],
                    }
                )
            )
        ]
    )
    reviewer = agents.ReviewerAgent(driver=driver, model="test/reviewer")

    decision = asyncio.run(
        reviewer.review(
            _task(),
            _passed_verification(),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert decision.decision is models.ReviewOutcome.PASS
    assert len(driver.requests) == 1
    request = driver.requests[0]
    assert request.role is models.AgentRole.REVIEWER
    assert request.tools == []
    packet = request.messages[1].content
    assert "AUTH-REVIEW-001" in packet
    assert '"passed": true' in packet
    assert "return token.startswith('signed:')" in packet
    assert "Actual Git diff" in packet
    assert "untrusted" in request.messages[0].content


def test_reviewer_can_reject_semantic_bug_after_hard_checks_pass(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    driver = FakeDriver(
        [
            _response(
                json.dumps(
                    {
                        "decision": "CHANGES_REQUESTED",
                        "summary": "The implementation bypasses token validation.",
                        "issues": [
                            {
                                "severity": "high",
                                "message": "verify_token always returns true, accepting invalid tokens.",
                                "file": "auth.py",
                                "line": 2,
                            }
                        ],
                    }
                )
            )
        ]
    )
    reviewer = agents.ReviewerAgent(driver=driver, model="test/reviewer")

    decision = asyncio.run(
        reviewer.review(
            _task(),
            _passed_verification(),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert decision.decision is models.ReviewOutcome.CHANGES_REQUESTED
    assert decision.issues[0].severity is models.ReviewSeverity.HIGH
    assert decision.issues[0].file == "auth.py"


def test_reviewer_repairs_invalid_structured_output_once(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return token == 'valid'\n\n# refactored\n",
        encoding="utf-8",
    )
    driver = FakeDriver(
        [
            _response("review looks good"),
            _response(
                json.dumps(
                    {
                        "decision": "PASS",
                        "summary": "No semantic issue found in the verified diff.",
                        "issues": [],
                    }
                )
            ),
        ]
    )
    reviewer = agents.ReviewerAgent(
        driver=driver,
        model="test/reviewer",
        max_schema_repair_attempts=1,
    )

    decision = asyncio.run(
        reviewer.review(
            _task(),
            _passed_verification(),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert decision.decision is models.ReviewOutcome.PASS
    assert len(driver.requests) == 2
    assert driver.requests[1].temperature == 0.0
    assert driver.requests[1].tools == []
    assert "Pydantic validation error" in driver.requests[1].messages[1].content


def test_reviewer_rejects_invalid_output_after_repair_budget(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    driver = FakeDriver([_response("not-json"), _response("still-not-json")])
    reviewer = agents.ReviewerAgent(
        driver=driver,
        model="test/reviewer",
        max_schema_repair_attempts=1,
    )

    with pytest.raises(agents.InvalidReviewerOutputError) as exc_info:
        asyncio.run(
            reviewer.review(
                _task(),
                _passed_verification(),
                workspace=LocalGitWorkspace(root),
            )
        )

    failure = exc_info.value.failure
    assert failure.failure_type is models.FailureType.INVALID_AGENT_OUTPUT
    assert failure.source is models.FailureSource.REVIEW
    assert failure.retryable is False
    assert len(driver.requests) == 2


def test_reviewer_rejects_empty_diff_before_calling_driver(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    driver = FakeDriver([])
    reviewer = agents.ReviewerAgent(driver=driver, model="test/reviewer")

    with pytest.raises(ValueError, match="non-empty Git diff"):
        asyncio.run(
            reviewer.review(
                _task(),
                _passed_verification(),
                workspace=LocalGitWorkspace(root),
            )
        )

    assert driver.requests == []


def test_workspace_unified_diff_includes_untracked_text_file(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "helpers.py").write_text(
        "def normalize(token: str) -> str:\n    return token.strip()\n",
        encoding="utf-8",
    )

    diff = LocalGitWorkspace(root).unified_diff()

    assert "--- /dev/null" in diff
    assert "+++ b/helpers.py" in diff
    assert "+def normalize(token: str) -> str:" in diff


def test_reviewer_does_not_mutate_repository(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "auth.py").write_text(
        "def verify_token(token: str) -> bool:\n    return token == 'valid'\n\n# comment\n",
        encoding="utf-8",
    )
    workspace = LocalGitWorkspace(root)
    before = workspace.unified_diff()
    driver = FakeDriver(
        [
            _response(
                json.dumps(
                    {
                        "decision": "PASS",
                        "summary": "The verified change is semantically acceptable.",
                        "issues": [],
                    }
                )
            )
        ]
    )

    asyncio.run(
        agents.ReviewerAgent(driver=driver, model="test/reviewer").review(
            _task(),
            _passed_verification(),
            workspace=workspace,
        )
    )

    assert workspace.unified_diff() == before
    assert driver.requests[0].tools == []
