from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from app import agents, models
from app.models.trace import TraceSpanKind
from app.providers.base import AgentDriver
from app.trace.collector import TaskTraceCollector
from app.workspace import LocalGitWorkspace


class _RecordingDriver(AgentDriver):
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[models.AgentRequest] = []
        self.progress_outcomes: list[bool] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        return next(self._responses)

    async def record_tool_outcome(
        self,
        *,
        role,
        calls,
        results,
        has_real_progress: bool,
        compacted_code_mutation: bool,
    ) -> None:
        del role, calls, results, compacted_code_mutation
        self.progress_outcomes.append(has_real_progress)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/developer",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _call(call_id: str, name: str, arguments: dict) -> models.ToolCall:
    return models.ToolCall(
        id=call_id,
        name=name,
        arguments=json.dumps(arguments),
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "gomoku_logic.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task(*, writable_files: list[str] | None = None) -> models.TaskContract:
    return models.TaskContract(
        task_id="developer-convergence",
        objective="Implement the requested repository change.",
        readable_files=["**"],
        writable_files=writable_files or ["src/gomoku_logic.py"],
        readonly_files=[],
        acceptance_criteria=["The requested repository change exists."],
        verification_commands=["python3 -c \"print('ok')\""],
        max_retries=1,
    )


def _developer(
    driver: _RecordingDriver,
    *,
    stuck_detector_enabled: bool = False,
) -> agents.DeveloperAgent:
    return agents.DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_iterations=10,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=False,
        runtime_stuck_detector_enabled=stuck_detector_enabled,
    )


def _prompt(request: models.AgentRequest) -> str:
    return "\n".join(message.content for message in request.messages)


def test_progress_credit_is_scoped_to_the_previous_real_mutation_turn(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(tool_calls=[_call("list", "list_files", {"directory": "src"})]),
            _response(
                tool_calls=[
                    _call(
                        "write",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
            _response(content=("已修改文件: src/gomoku_logic.py\n已执行验证: 无\n遗留事项: 无")),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert driver.progress_outcomes == [False, True, False]
    assert [request.budget_progress for request in driver.requests] == [
        False,
        False,
        True,
        False,
    ]
    assert [request.liveness_credit for request in driver.requests] == [
        models.LivenessCredit.INITIAL_STARTUP,
        models.LivenessCredit.NORMAL,
        models.LivenessCredit.VERIFIED_PROGRESS,
        models.LivenessCredit.NORMAL,
    ]


def test_identical_write_file_is_successful_tool_but_not_real_progress(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    write = _call(
        "write-1",
        "write_file",
        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
    )
    same_write = _call(
        "write-2",
        "write_file",
        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
    )
    driver = _RecordingDriver(
        [
            _response(tool_calls=[write]),
            _response(tool_calls=[same_write]),
            _response(content=("已修改文件: src/gomoku_logic.py\n已执行验证: 无\n遗留事项: 无")),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert driver.progress_outcomes == [True, False]
    assert driver.requests[1].budget_progress is True
    assert driver.requests[2].budget_progress is False
    assert driver.requests[2].liveness_credit is models.LivenessCredit.NORMAL


def test_same_file_second_mutation_turn_emits_repeated_convergence_nudge(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-2",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-3",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 3\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-4",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 4\n"},
                    )
                ]
            ),
            _response(content=("已修改文件: src/gomoku_logic.py\n已执行验证: 无\n遗留事项: 无")),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert "Re-evaluate the TaskContract" in _prompt(driver.requests[1])
    assert "several consecutive turns" in _prompt(driver.requests[2])
    assert "several consecutive turns" in _prompt(driver.requests[3])
    assert driver.progress_outcomes == [True, True, True]


def test_mutation_then_model_stop_hands_candidate_to_verification(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(content=("已修改文件: src/gomoku_logic.py\n已执行验证: 无\n遗留事项: 无")),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert result.changed_files == ["src/gomoku_logic.py"]
    assert len(driver.requests) == 2
    assert "Re-evaluate the TaskContract" in _prompt(driver.requests[1])


def test_complex_task_can_continue_across_multiple_real_mutation_turns(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-a",
                        "write_file",
                        {"path": "src/a.py", "content": "A = 1\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-b",
                        "write_file",
                        {"path": "src/b.py", "content": "B = 1\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-c",
                        "write_file",
                        {"path": "src/c.py", "content": "C = 1\n"},
                    )
                ]
            ),
            _response(
                content=("已修改文件: src/a.py, src/b.py, src/c.py\n已执行验证: 无\n遗留事项: 无")
            ),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert result.changed_files == ["src/a.py", "src/b.py", "src/c.py"]
    assert len(driver.requests) == 4
    assert driver.progress_outcomes == [True, True, True]
    assert "several consecutive turns" in _prompt(driver.requests[3])


def test_candidate_handoff_preempts_repeated_post_mutation_observations(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    write = _call(
        "write",
        "write_file",
        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
    )
    searches = [
        _call(
            f"search-{index}",
            "search_code",
            {"query": "VALUE", "directory": "src", "max_results": 5},
        )
        for index in range(4)
    ]
    driver = _RecordingDriver(
        [_response(tool_calls=[write])] + [_response(tool_calls=[search]) for search in searches]
    )

    result = asyncio.run(
        _developer(driver, stuck_detector_enabled=True).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert result.changed_files == ["src/gomoku_logic.py"]
    assert len(driver.requests) == 3
    assert driver.progress_outcomes == [True, False, False]
    assert "deterministic verification" in result.final_message


def test_real_mutation_resets_post_mutation_observation_handoff_counter(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-core-2",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-core-2",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-helper",
                        "write_file",
                        {"path": "src/helper.py", "content": "HELPER = True\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-helper",
                        "read_range",
                        {"path": "src/helper.py", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-core-final",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 5
    assert driver.progress_outcomes == [True, False, True, False, False]
    assert result.changed_files == ["src/gomoku_logic.py", "src/helper.py"]


def test_candidate_handoff_terminal_turn_records_runtime_progress_trace(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-1",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-2",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
        ]
    )
    trace = TaskTraceCollector(
        run_id=uuid4(),
        task_id="developer-convergence",
        dispatch_id=uuid4(),
        generation=1,
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
            trace=trace,
        )
    )

    terminal_turn = next(
        span
        for span in trace.batch().spans
        if span.agent_role is models.AgentRole.DEVELOPER and span.iteration == 3
    )
    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert terminal_turn.has_workspace_patch is True
    assert terminal_turn.turn_made_progress is False
    assert terminal_turn.changed_files_this_turn == ()


def test_single_exact_candidate_requires_one_observation_before_handoff(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-ready",
                        "write_file",
                        {"path": "src/gomoku_logic.py", "content": "VALUE = 2\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ready",
                        "read_range",
                        {
                            "path": "src/gomoku_logic.py",
                            "start_line": 1,
                            "end_line": 5,
                        },
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/gomoku_logic.py"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 2
    assert driver.progress_outcomes == [True, False]
    assert result.changed_files == ["src/gomoku_logic.py"]
    assert "first observation-only turn" in result.final_message


def test_incomplete_exact_candidate_does_not_handoff_until_all_deliverables_change(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>board</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-index-1",
                        "read_range",
                        {"path": "src/index.html", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-index-2",
                        "read_range",
                        {"path": "src/index.html", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ui",
                        "read_range",
                        {"path": "src/gomoku_ui.js", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 4
    assert driver.progress_outcomes == [True, False, False, True]
    assert result.changed_files == ["src/gomoku_ui.js", "src/index.html"]
    assert "immediately after the mutation turn" in result.final_message


def test_glob_write_scope_preserves_p14_two_observation_handoff(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-helper",
                        "write_file",
                        {"path": "src/helper.py", "content": "HELPER = True\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-helper-1",
                        "read_range",
                        {"path": "src/helper.py", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-helper-2",
                        "read_range",
                        {"path": "src/helper.py", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/**"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    assert driver.progress_outcomes == [True, False, False]
    assert result.changed_files == ["src/helper.py"]
    assert "after 2 consecutive observation-only turns" in result.final_message


def test_completion_mutation_with_tool_failure_does_not_handoff_early(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>board</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    ),
                    _call(
                        "read-missing",
                        "read_range",
                        {"path": "src/missing.py", "start_line": 1, "end_line": 5},
                    ),
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ready",
                        "read_range",
                        {"path": "src/gomoku_ui.js", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    assert driver.progress_outcomes == [True, True, False]
    assert result.changed_files == ["src/gomoku_ui.js", "src/index.html"]
    assert "first observation-only turn" in result.final_message



def test_first_turn_that_writes_all_exact_deliverables_does_not_handoff_immediately(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>board</main>\n"},
                    ),
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    ),
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "read-ui",
                        "read_range",
                        {"path": "src/gomoku_ui.js", "start_line": 1, "end_line": 5},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 2
    assert driver.progress_outcomes == [True, False]
    assert "first observation-only turn" in result.final_message



def test_deliverable_completion_mode_focuses_missing_exact_path(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    completion_prompt = _prompt(driver.requests[2])
    assert "DELIVERABLE COMPLETION MODE" in completion_prompt
    assert "src/index.html" in completion_prompt
    assert "src/gomoku_ui.js" in completion_prompt
    assert "immediately after the mutation turn" in result.final_message


def test_deliverable_completion_mode_allows_one_bounded_correction(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-3",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v3</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 4
    assert "FINAL BOUNDED CHANCE" in _prompt(driver.requests[3])
    assert "src/gomoku_ui.js" in _prompt(driver.requests[3])
    assert result.changed_files == ["src/gomoku_ui.js", "src/index.html"]


def test_deliverable_completion_gate_stops_second_unproductive_rewrite(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-3",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v3</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-4",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v4</main>\n"},
                    )
                ]
            ),
            _response(content="must not be requested"),
        ]
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
        )
    )

    assert result.stop_reason is models.DeveloperStopReason.NO_PROGRESS
    assert len(driver.requests) == 4
    assert driver.progress_outcomes == [True, True, True, True]
    assert "src/gomoku_ui.js" in result.final_message
    assert result.changed_files == ["src/index.html"]


def test_deliverable_completion_trace_records_structural_progress(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    driver = _RecordingDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-index-1",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v1</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-index-2",
                        "write_file",
                        {"path": "src/index.html", "content": "<main>v2</main>\n"},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call(
                        "write-ui",
                        "write_file",
                        {"path": "src/gomoku_ui.js", "content": "const SIZE = 15;\n"},
                    )
                ]
            ),
        ]
    )
    trace = TaskTraceCollector(
        run_id=uuid4(),
        task_id="developer-convergence",
        dispatch_id=uuid4(),
        generation=1,
    )

    result = asyncio.run(
        _developer(driver).run(
            _task(writable_files=["src/index.html", "src/gomoku_ui.js"]),
            workspace=LocalGitWorkspace(root),
            trace=trace,
        )
    )
    turns = {
        span.iteration: span
        for span in trace.batch().spans
        if span.agent_role is models.AgentRole.DEVELOPER
        and span.kind is TraceSpanKind.AGENT_TURN
    }

    assert result.stop_reason is models.DeveloperStopReason.MODEL_STOP
    assert turns[2].candidate_readiness_known is True
    assert turns[2].candidate_ready is False
    assert turns[2].missing_required_deliverables == ("src/gomoku_ui.js",)
    assert turns[2].deliverable_progress is False
    assert turns[2].deliverable_completion_mode is True
    assert turns[2].deliverable_convergence_violations == 0
    assert turns[3].candidate_ready is True
    assert turns[3].missing_required_deliverables == ()
    assert turns[3].deliverable_progress is True
    assert turns[3].deliverable_completion_mode is False
