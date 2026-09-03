from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from app.agents import DeveloperAgent
from app.integrations.openhands import OpenHandsStuckAdapter, StuckPattern
from app.models.agent import AgentResponse, TokenUsage
from app.models.developer import DeveloperStopReason
from app.models.task import TaskContract
from app.models.tools import ToolCall, ToolErrorCode, ToolExecutionResult
from app.workspace import LocalGitWorkspace


def _call(call_id: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def _ok(call: ToolCall, content: str = "same observation") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=True,
        content=content,
    )


def _error(call: ToolCall, content: str = "invalid arguments") -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=False,
        content=content,
        error_code=ToolErrorCode.INVALID_ARGUMENTS,
    )


def test_repeating_action_observation_matches_openhands_threshold() -> None:
    detector = OpenHandsStuckAdapter(enabled=True)

    for index in range(3):
        call = _call(str(index), "search_code", {"query": "VALUE"})
        detector.record_tool_result(call, _ok(call))
        assert detector.inspect().reason is None

    call = _call("4", "search_code", {"query": "VALUE"})
    detector.record_tool_result(call, _ok(call))
    decision = detector.inspect()

    assert decision.reason is StuckPattern.REPEATING_ACTION_OBSERVATION
    assert decision.should_stop is True


def test_different_action_arguments_do_not_collapse_to_same_signature() -> None:
    detector = OpenHandsStuckAdapter(enabled=True)

    for index, query in enumerate(("ONE", "TWO", "THREE", "FOUR")):
        call = _call(str(index), "search_code", {"query": query})
        detector.record_tool_result(call, _ok(call))

    assert detector.inspect().reason is None


def test_action_error_nudges_at_three_then_stops_after_fourth_repeat() -> None:
    detector = OpenHandsStuckAdapter(enabled=True)
    call = _call("template", "apply_patch", {"patch": "*** invalid ***"})

    for index in range(2):
        current = call.model_copy(update={"id": str(index)})
        detector.record_tool_result(current, _error(current))
        assert detector.inspect().reason is None

    third = call.model_copy(update={"id": "3"})
    detector.record_tool_result(third, _error(third))
    nudge = detector.inspect()

    assert nudge.reason is StuckPattern.REPEATING_ACTION_ERROR
    assert nudge.nudge is not None
    assert nudge.should_stop is False
    assert "apply_patch" in nudge.nudge
    assert detector.inspect().reason is None

    fourth = call.model_copy(update={"id": "4"})
    detector.record_tool_result(fourth, _error(fourth))
    stopped = detector.inspect()

    assert stopped.reason is StuckPattern.REPEATING_ACTION_ERROR
    assert stopped.should_stop is True


def test_alternating_action_observation_pattern_is_detected() -> None:
    detector = OpenHandsStuckAdapter(enabled=True)

    for index in range(6):
        label = "A" if index % 2 == 0 else "B"
        call = _call(str(index), "search_code", {"query": label})
        detector.record_tool_result(call, _ok(call, content=f"result-{label}"))

    decision = detector.inspect()

    assert decision.reason is StuckPattern.ALTERNATING_ACTION_OBSERVATION
    assert decision.should_stop is True


class _FakeDriver:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self._responses = iter(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return next(self._responses)


def _response(call: ToolCall) -> AgentResponse:
    return AgentResponse(
        model="test/developer",
        content="",
        tool_calls=[call],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=1,
        finish_reason="tool_calls",
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "devflow@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "DevFlow Tests"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return root


def test_agent_loop_v3_stops_repeated_successful_observation_with_openhands_detector(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    task = TaskContract(
        task_id="stuck-runtime",
        objective="Change VALUE from 1 to 2.",
        readable_files=["src/**"],
        writable_files=["src/**"],
        readonly_files=[],
        acceptance_criteria=["src/main.py contains VALUE = 2"],
        verification_commands=["python3 -c \"import src.main\""],
        max_retries=1,
    )
    calls = [
        _call(
            f"search-{index}",
            "search_code",
            {"query": "VALUE", "directory": "src", "max_results": 5},
        )
        for index in range(4)
    ]
    driver = _FakeDriver([_response(call) for call in calls])
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_iterations=6,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=False,
        runtime_stuck_detector_enabled=True,
    )

    result = asyncio.run(
        developer.run(
            task,
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is DeveloperStopReason.NO_PROGRESS
    assert result.changed_files == []
    assert len(driver.requests) == 4
