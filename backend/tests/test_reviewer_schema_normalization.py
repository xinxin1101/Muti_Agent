import asyncio
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeDriver received more calls than expected")
        return self._responses.pop(0)


def _response(content: str) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/reviewer",
        content=content,
        latency_ms=1,
        finish_reason="stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _workspace(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "reviewer-test@devflow.local")
    _git(root, "config", "user.name", "Reviewer Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    return LocalGitWorkspace(root)


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="REVIEW-SCHEMA-001",
        objective="Change VALUE without introducing semantic regressions.",
        readable_files=["app.py"],
        writable_files=["app.py"],
        readonly_files=[],
        acceptance_criteria=["VALUE must equal 2."],
        verification_commands=["python -c 'assert True'"],
    )


def _verification() -> models.VerificationResult:
    return models.VerificationResult(
        passed=True,
        checks=[
            models.CheckResult(
                check_type=models.CheckType.TEST,
                name="acceptance",
                command="python -c 'assert True'",
                passed=True,
                exit_code=0,
            )
        ],
    )


def test_reviewer_prompt_uses_exact_line_field_schema() -> None:
    reviewer = agents.ReviewerAgent(driver=FakeDriver([]), model="test/reviewer")

    prompt = reviewer._reviewer_system_prompt()

    assert '"file":"src/foo.py","line":123' in prompt
    assert "optional positive line" not in prompt
    assert "Never emit positive_line" in prompt


def test_positive_line_alias_is_normalized_without_second_model_call(tmp_path: Path) -> None:
    driver = FakeDriver(
        [
            _response(
                json.dumps(
                    {
                        "decision": "CHANGES_REQUESTED",
                        "summary": "The changed branch is semantically wrong.",
                        "issues": [
                            {
                                "severity": "high",
                                "message": "The changed value violates the semantic contract.",
                                "file": "app.py",
                                "positive_line": 1,
                            }
                        ],
                    }
                )
            )
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
            _verification(),
            workspace=_workspace(tmp_path),
        )
    )

    assert len(driver.requests) == 1
    assert decision.decision is models.ReviewOutcome.CHANGES_REQUESTED
    assert decision.issues[0].file == "app.py"
    assert decision.issues[0].line == 1


def test_markdown_fence_is_removed_without_second_model_call(tmp_path: Path) -> None:
    content = "```json\n" + json.dumps(
        {
            "decision": "PASS",
            "summary": "No semantic regression remains.",
            "issues": [],
        }
    ) + "\n```"
    driver = FakeDriver([_response(content)])
    reviewer = agents.ReviewerAgent(
        driver=driver,
        model="test/reviewer",
        max_schema_repair_attempts=1,
    )

    decision = asyncio.run(
        reviewer.review(
            _task(),
            _verification(),
            workspace=_workspace(tmp_path),
        )
    )

    assert len(driver.requests) == 1
    assert decision.decision is models.ReviewOutcome.PASS


def test_ambiguous_line_alias_is_not_silently_normalized() -> None:
    content = json.dumps(
        {
            "decision": "CHANGES_REQUESTED",
            "summary": "Conflicting location evidence.",
            "issues": [
                {
                    "severity": "high",
                    "message": "Conflicting line fields must remain invalid.",
                    "file": "app.py",
                    "line": 1,
                    "positive_line": 2,
                }
            ],
        }
    )

    assert agents.ReviewerAgent._deterministically_normalize_output(content) is None
