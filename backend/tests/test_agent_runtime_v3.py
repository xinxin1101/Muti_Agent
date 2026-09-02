from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.providers.base import AgentDriver
from app.workspace import LocalGitWorkspace


class FakeDriver(AgentDriver):
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self._responses = iter(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        return next(self._responses)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/repair",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        latency_ms=5,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path, content: str) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "gomoku_logic.py").write_text(content, encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="gomoku-core",
        objective="Provide the GameEngine API required by deterministic verification.",
        readable_files=["**"],
        writable_files=["src/gomoku_logic.py"],
        readonly_files=[],
        acceptance_criteria=[
            "src.gomoku_logic.GameEngine is importable",
            "GameEngine().is_valid_move(7, 7) returns true",
        ],
        verification_commands=[
            (
                'python3 -c "from src.gomoku_logic import GameEngine; '
                'e = GameEngine(); assert e.is_valid_move(7, 7)"'
            )
        ],
        max_retries=2,
    )


def _failure() -> models.FailureReport:
    return models.FailureReport(
        failure_type=models.FailureType.TEST_FAILURE,
        source=models.FailureSource.VERIFICATION,
        message="Deterministic custom verification failed.",
        retryable=True,
        evidence=[
            (
                "ImportError: cannot import name 'GameEngine' "
                "from 'src.gomoku_logic' (/workspace/src/gomoku_logic.py)"
            )
        ],
    )


def _handoff(task: models.TaskContract, workspace: LocalGitWorkspace) -> models.RepairHandoff:
    failure = _failure()
    return models.RepairHandoff(
        task_id=task.task_id,
        objective=task.objective,
        repository_head=workspace.head_commit(),
        acceptance_criteria=tuple(task.acceptance_criteria),
        verification_commands=tuple(task.verification_commands),
        writable_files=tuple(task.writable_files),
        readonly_files=tuple(task.readonly_files),
        changed_files=(),
        relevant_paths=("src/gomoku_logic.py",),
        failure_kind=models.RepairFailureKind.IMPORT_SYMBOL_MISSING,
        suspected_path="src/gomoku_logic.py",
        suspected_symbol="GameEngine",
        failures=(
            models.RepairFailureDigest(
                failure_type=failure.failure_type,
                source=failure.source,
                message=failure.message,
                evidence=tuple(failure.evidence),
            ),
        ),
    )


def _load_gomoku_module(path: Path):
    spec = importlib.util.spec_from_file_location("runtime_v3_gomoku_logic", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_v3_import_prefetch_drives_gameengine_patch(tmp_path: Path) -> None:
    original = (
        "class Board:\n"
        "    def is_valid_move(self, row, col):\n"
        "        return False\n"
    )
    replacement = (
        "class GameEngine:\n"
        "    def is_valid_move(self, row, col):\n"
        "        return 0 <= row < 15 and 0 <= col < 15\n"
    )
    root = _repository(tmp_path, original)
    workspace = LocalGitWorkspace(root)
    task = _task()
    patch = models.ToolCall(
        id="repair-gameengine",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "src/gomoku_logic.py",
                "old_text": original,
                "new_text": replacement,
            }
        ),
    )
    driver = FakeDriver(
        [
            _response(tool_calls=[patch]),
            _response(content="已修改文件: src/gomoku_logic.py"),
        ]
    )
    repair = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=True,
    )

    result = asyncio.run(
        repair.repair(
            task,
            [_failure()],
            attempt=1,
            workspace=workspace,
            handoff=_handoff(task, workspace),
        )
    )

    assert result.stop_reason is models.RepairStopReason.MODEL_STOP
    assert result.changed_files == ["src/gomoku_logic.py"]
    assert len(driver.requests) == 2
    first_prompt = "\n".join(message.content for message in driver.requests[0].messages)
    assert "Deterministic Repair prefetch" in first_prompt
    assert "failure_kind=IMPORT_SYMBOL_MISSING" in first_prompt
    assert "path=src/gomoku_logic.py" in first_prompt
    assert "symbol=GameEngine" in first_prompt
    assert "symbol_found=False" in first_prompt
    assert "class Board:" in first_prompt
    assert driver.requests[0].context_estimated_tokens == 0

    module = _load_gomoku_module(root / "src" / "gomoku_logic.py")
    assert module.GameEngine().is_valid_move(7, 7) is True


def test_runtime_v3_forces_mutation_after_two_observation_turns(tmp_path: Path) -> None:
    original = "VALUE = 1\n"
    root = _repository(tmp_path, original)
    target = root / "src" / "gomoku_logic.py"
    workspace = LocalGitWorkspace(root)
    task = _task().model_copy(
        update={
            "objective": "Change VALUE from 1 to 2.",
            "acceptance_criteria": ["src/gomoku_logic.py contains VALUE = 2"],
            "verification_commands": ["python3 -c \"import src.gomoku_logic\""],
        }
    )
    handoff = _handoff(task, workspace).model_copy(
        update={
            "failure_kind": None,
            "suspected_path": None,
            "suspected_symbol": None,
        }
    )
    search = models.ToolCall(
        id="observe-search",
        name="search_code",
        arguments=json.dumps({"query": "VALUE", "directory": "src", "max_results": 5}),
    )
    read = models.ToolCall(
        id="observe-read",
        name="read_range",
        arguments=json.dumps(
            {"path": "src/gomoku_logic.py", "start_line": 1, "end_line": 10}
        ),
    )
    patch = models.ToolCall(
        id="mutate",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "src/gomoku_logic.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
            }
        ),
    )
    driver = FakeDriver(
        [
            _response(tool_calls=[search]),
            _response(tool_calls=[read]),
            _response(tool_calls=[patch]),
            _response(content="已修改文件: src/gomoku_logic.py"),
        ]
    )
    repair = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=False,
    )

    result = asyncio.run(
        repair.repair(
            task,
            [_failure()],
            attempt=1,
            workspace=workspace,
            handoff=handoff,
        )
    )

    assert result.stop_reason is models.RepairStopReason.MODEL_STOP
    assert len(driver.requests) == 4
    gate_prompt = "\n".join(message.content for message in driver.requests[2].messages)
    assert "no workspace mutation exists yet" in gate_prompt
    assert "Use apply_patch or write_file" in gate_prompt
    assert target.read_text(encoding="utf-8") == "VALUE = 2\n"


def test_runtime_v3_stops_repeated_observation_after_mutation_gate(tmp_path: Path) -> None:
    root = _repository(tmp_path, "VALUE = 1\n")
    workspace = LocalGitWorkspace(root)
    task = _task()
    handoff = _handoff(task, workspace).model_copy(
        update={
            "failure_kind": None,
            "suspected_path": None,
            "suspected_symbol": None,
        }
    )
    observations = [
        models.ToolCall(
            id=f"observe-{index}",
            name="search_code",
            arguments=json.dumps(
                {"query": "VALUE", "directory": "src", "max_results": 5}
            ),
        )
        for index in range(1, 5)
    ]
    driver = FakeDriver([_response(tool_calls=[call]) for call in observations])
    repair = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=False,
    )

    result = asyncio.run(
        repair.repair(
            task,
            [_failure()],
            attempt=1,
            workspace=workspace,
            handoff=handoff,
        )
    )

    assert result.stop_reason is models.RepairStopReason.NO_PROGRESS
    assert result.changed_files == []
    assert len(driver.requests) == 4
    assert any(
        "MUTATION REQUIRED" in message.content
        for message in driver.requests[3].messages
    )
