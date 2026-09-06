import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app import agents, models
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, response: models.AgentResponse) -> None:
        self.response = response
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        return self.response


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _workspace(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    (root / "module.py").write_text("VALUE = 2  # reviewed\n", encoding="utf-8")
    return LocalGitWorkspace(root)


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="CLOSURE-001",
        objective="Change VALUE to 2 and close the prior semantic blocker.",
        readable_files=["**"],
        writable_files=["module.py"],
        acceptance_criteria=["module.py contains VALUE = 2"],
        verification_commands=["pytest -q"],
    )


def _verification() -> models.VerificationResult:
    return models.VerificationResult(
        passed=True,
        checks=[
            models.CheckResult(
                check_type=models.CheckType.TEST,
                name="pytest",
                passed=True,
            )
        ],
    )


def _previous_rejection() -> models.ReviewDecision:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.CHANGES_REQUESTED,
        summary="The reviewed marker is missing.",
        issues=[
            models.ReviewIssue(
                severity=models.ReviewSeverity.MEDIUM,
                message="Add the required reviewed marker.",
                file="module.py",
                line=1,
            )
        ],
    )


def test_closure_context_requires_prior_rejection() -> None:
    with pytest.raises(ValueError, match="prior CHANGES_REQUESTED"):
        models.ReviewerClosureContext(
            review_round=2,
            previous_decision=models.ReviewDecision(
                decision=models.ReviewOutcome.PASS,
                summary="Already passed.",
                issues=[],
            ),
            repair_attempt_start=1,
            repair_attempt_end=1,
            repair_changed_files=("module.py",),
            patch_hash_before="a" * 64,
            patch_hash_after="b" * 64,
            repair_delta="-VALUE = 1\n+VALUE = 2",
        )


def test_reviewer_closure_mode_preserves_full_diff_and_focuses_prior_blocker(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    closure = models.ReviewerClosureContext(
        review_round=2,
        previous_decision=_previous_rejection(),
        repair_attempt_start=1,
        repair_attempt_end=1,
        repair_changed_files=("module.py",),
        patch_hash_before="a" * 64,
        patch_hash_after="b" * 64,
        repair_delta=(
            "--- a/module.py\n+++ b/module.py\n"
            "-VALUE = 2\n+VALUE = 2  # reviewed"
        ),
    )
    driver = FakeDriver(
        models.AgentResponse(
            model="fake/reviewer",
            content=json.dumps(
                {
                    "decision": "PASS",
                    "summary": "The prior blocker is closed and no new blocker is evidenced.",
                    "issues": [],
                }
            ),
            latency_ms=1,
        )
    )
    decision = asyncio.run(
        agents.ReviewerAgent(driver=driver, model="fake/reviewer").review(
            _task(),
            _verification(),
            workspace=workspace,
            closure_context=closure,
        )
    )

    assert decision.decision is models.ReviewOutcome.PASS
    assert len(driver.requests) == 1
    request = driver.requests[0]
    system = request.messages[0].content
    packet = request.messages[1].content
    assert "CLOSURE REVIEW MODE" in system
    assert "First re-evaluate every prior blocking issue" in system
    assert "style, naming, documentation polish" in system
    assert "ReviewerClosureContext metadata" in packet
    assert '"review_round": 2' in packet
    assert '"repair_changed_files"' in packet
    assert "Add the required reviewed marker" in packet
    assert "Repair delta since the previous rejected review" in packet
    assert "+VALUE = 2  # reviewed" in packet
    assert "Actual Git diff" in packet
    assert '"passed": true' in packet
