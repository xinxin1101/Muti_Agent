import asyncio
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.models.run import TaskRunState
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self.responses = list(responses)

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        del request
        if not self.responses:
            raise AssertionError("required demo received an unexpected model call")
        return self.responses.pop(0)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
        ),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_required_demo_normal_success(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-demo@example.com")
    _git(root, "config", "user.name", "DevFlow Demo")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    patch = models.ToolCall(
        id="demo-write",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
            }
        ),
    )
    developer = FakeDriver(
        [
            _response(tool_calls=[patch]),
            _response(content="Implementation complete."),
        ]
    )
    reviewer = FakeDriver(
        [
            _response(
                content=models.ReviewDecision(
                    decision=models.ReviewOutcome.PASS,
                    summary="The verified implementation satisfies the task.",
                    issues=[],
                ).model_dump_json()
            )
        ]
    )
    repair = FakeDriver([])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer, model="fake/developer"),
        verifier=DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=reviewer, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=repair, model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )
    task = models.TaskContract(
        task_id="DEMO-NORMAL",
        objective="Change VALUE from 1 to 2.",
        readable_files=["module.py"],
        writable_files=["module.py"],
        readonly_files=[],
        acceptance_criteria=["module.py contains VALUE = 2"],
        verification_commands=[
            "python -c \"from pathlib import Path; "
            "assert Path('module.py').read_text(encoding='utf-8') == 'VALUE = 2\\n'\""
        ],
        max_retries=1,
    )

    result = asyncio.run(
        orchestrator.run(task, workspace=LocalGitWorkspace(root))
    )

    assert result.status is TaskRunState.SUCCEEDED
    assert result.repair_attempts == 0
    assert len(result.verifications) == 1
    assert result.verifications[0].passed is True
    assert len(result.reviews) == 1
    assert result.reviews[0].decision is models.ReviewOutcome.PASS
    assert repair.responses == []
