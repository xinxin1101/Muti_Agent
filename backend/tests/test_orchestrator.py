import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app import agents, models
from app.models.run import TaskRunState
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.runtime.state_machine import TaskStateMachine
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeDriver received an unexpected model call")
        return self.responses.pop(0)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
    prompt_tokens: int = 4,
    completion_tokens: int = 2,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        latency_ms=3,
        finish_reason="tool_calls" if tool_calls else "stop",
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
    tests = root / "tests"
    tests.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_value.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_value():\n"
        "    text = Path('module.py').read_text(encoding='utf-8')\n"
        "    assert 'VALUE = 2' in text\n",
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
        task_id="RUN-001",
        objective="Change VALUE to 2 while keeping tests protected.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["module.py contains VALUE = 2"],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=max_retries,
    )


def _patch(call_id: str, old: str, new: str) -> models.ToolCall:
    return models.ToolCall(
        id=call_id,
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": old,
                "new_text": new,
            }
        ),
    )


def _review_pass() -> str:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.PASS,
        summary="The implementation satisfies the task and verified diff.",
        issues=[],
    ).model_dump_json()


def _orchestrator(
    *,
    developer_driver: FakeDriver,
    repair_driver: FakeDriver,
    reviewer_driver: FakeDriver,
) -> SingleTaskOrchestrator:
    return SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=repair_driver, model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )


def test_complete_loop_repairs_first_test_failure_then_succeeds(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 3", "VALUE = 2")]),
            _response(content="Fixed the failing assertion target."),
        ]
    )
    reviewer_driver = FakeDriver([_response(content=_review_pass())])

    result = asyncio.run(
        _orchestrator(
            developer_driver=developer_driver,
            repair_driver=repair_driver,
            reviewer_driver=reviewer_driver,
        ).run(_task(), workspace=workspace)
    )

    assert result.status is TaskRunState.SUCCEEDED
    assert result.changed_files == ["module.py"]
    assert result.repair_attempts == 1
    assert len(result.verifications) == 2
    assert result.verifications[0].passed is False
    assert result.verifications[1].passed is True
    assert result.reviews[-1].decision is models.ReviewOutcome.PASS
    assert result.failures == []
    assert [event.state for event in result.events] == [
        TaskRunState.PENDING,
        TaskRunState.RUNNING,
        TaskRunState.VERIFYING,
        TaskRunState.REPAIRING,
        TaskRunState.VERIFYING,
        TaskRunState.REVIEWING,
        TaskRunState.SUCCEEDED,
    ]
    assert result.agent_models[models.AgentRole.DEVELOPER] == "fake/developer"
    assert {item.role for item in result.agent_usage} == {
        models.AgentRole.DEVELOPER,
        models.AgentRole.REPAIR,
    }
    assert "VALUE = 2" in (root / "module.py").read_text(encoding="utf-8")
    assert len(developer_driver.requests) == 2
    assert len(repair_driver.requests) == 2
    assert len(reviewer_driver.requests) == 1


def test_retry_budget_exhaustion_preserves_test_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 3", "VALUE = 4")]),
            _response(content="Repair attempt completed."),
        ]
    )
    reviewer_driver = FakeDriver([])

    result = asyncio.run(
        _orchestrator(
            developer_driver=developer_driver,
            repair_driver=repair_driver,
            reviewer_driver=reviewer_driver,
        ).run(_task(max_retries=1), workspace=workspace)
    )

    assert result.status is TaskRunState.FAILED
    assert result.repair_attempts == 1
    assert len(result.verifications) == 2
    assert result.failures[0].failure_type is models.FailureType.TEST_FAILURE
    assert result.failures[0].retryable is False
    assert "Repair retry budget was exhausted" in result.failures[0].message
    assert any(item == "repair_attempts_exhausted=1" for item in result.failures[0].evidence)
    assert reviewer_driver.requests == []


def test_state_machine_rejects_invalid_transition() -> None:
    machine = TaskStateMachine()

    with pytest.raises(ValueError, match="invalid task-state transition"):
        machine.transition(TaskRunState.REVIEWING, detail="skip hard gate")
