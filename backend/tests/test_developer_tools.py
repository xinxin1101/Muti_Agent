import asyncio
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents import DeveloperAgent
from app.models import (
    AgentResponse,
    DeveloperStopReason,
    LivenessCredit,
    MessageRole,
    TaskContract,
    TokenUsage,
    ToolCall,
    ToolErrorCode,
)
from app.providers.budgeted import BudgetedAgentDriver
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.tools import RepositoryToolbox
from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "app" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("def test_value(): pass\n", encoding="utf-8")
    (root / "docs" / "secret.md").write_text("not visible\n", encoding="utf-8")

    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> TaskContract:
    return TaskContract(
        task_id="DEV-001",
        objective="Change VALUE from 1 to 2.",
        readable_files=["app/**", "tests/**"],
        writable_files=["app/**"],
        readonly_files=["tests/**"],
        acceptance_criteria=["app/main.py contains VALUE = 2."],
        verification_commands=["pytest -q", "ruff check ."],
    )


def _call(call_id: str, name: str, arguments: dict) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))


def test_repository_tools_list_read_and_search_only_visible_files(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    listed = toolbox.execute(_call("1", "list_files", {}))
    read = toolbox.execute(_call("2", "read_file", {"path": "app/main.py"}))
    searched = toolbox.execute(_call("3", "search_code", {"query": "VALUE"}))

    assert listed.ok is True
    listed_payload = json.loads(listed.content)
    assert "app/main.py" in listed_payload["files"]
    assert "tests/test_main.py" in listed_payload["files"]
    assert "docs/secret.md" not in listed_payload["files"]

    assert read.ok is True
    assert json.loads(read.content)["content"] == "VALUE = 1\n"

    assert searched.ok is True
    matches = json.loads(searched.content)["matches"]
    assert matches[0]["path"] == "app/main.py"


def test_repository_tools_batch_read_and_search_reduce_exploration_round_trips(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    read = toolbox.execute(
        _call(
            "batch-read",
            "read_files",
            {"paths": ["app/main.py", "tests/test_main.py"]},
        )
    )
    searched = toolbox.execute(
        _call(
            "batch-search",
            "search_code_many",
            {"queries": ["VALUE", "test_value"]},
        )
    )

    assert read.ok is True
    files = json.loads(read.content)["files"]
    assert [item["path"] for item in files] == ["app/main.py", "tests/test_main.py"]
    assert files[0]["content"] == "VALUE = 1\n"

    assert searched.ok is True
    results = json.loads(searched.content)["results"]
    assert results[0]["query"] == "VALUE"
    assert results[0]["matches"][0]["path"] == "app/main.py"
    assert results[1]["query"] == "test_value"
    assert results[1]["matches"][0]["path"] == "tests/test_main.py"


def test_repository_tools_support_bounded_range_and_python_symbol_reads(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    (root / "app" / "main.py").write_text(
        (
            "import os\n\nclass Game:\n    def play(self):\n        return 1\n\n"
            "def helper():\n    return 2\n"
        ),
        encoding="utf-8",
    )
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    range_result = toolbox.execute(
        _call("range", "read_range", {"path": "app/main.py", "start_line": 3, "end_line": 5})
    )
    symbol_result = toolbox.execute(
        _call("symbol", "read_symbol", {"path": "app/main.py", "symbol": "Game.play"})
    )

    assert range_result.ok is True
    assert json.loads(range_result.content)["content"] == (
        "class Game:\n    def play(self):\n        return 1\n"
    )
    assert symbol_result.ok is True
    payload = json.loads(symbol_result.content)
    assert payload["symbol"] == "Game.play"
    assert payload["requested_start_line"] == 4
    assert payload["returned_end_line"] == 5
    assert "import os" in payload["content"]
    assert "def play" in payload["content"]


def test_progressive_read_tools_preserve_scope_and_validate_ranges(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    (root / "app" / "main.js").write_text("export const value = 1;\n", encoding="utf-8")
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    denied = toolbox.execute(
        _call("denied", "read_range", {"path": "docs/secret.md", "start_line": 1, "end_line": 2})
    )
    internal = toolbox.execute(
        _call("internal", "read_symbol", {"path": ".git/config", "symbol": "config"})
    )
    invalid = toolbox.execute(
        _call("invalid", "read_range", {"path": "app/main.py", "start_line": 4, "end_line": 2})
    )
    unsupported = toolbox.execute(
        _call("unsupported", "read_symbol", {"path": "app/main.js", "symbol": "value"})
    )

    assert denied.error_code is ToolErrorCode.PATH_DENIED
    assert internal.error_code is ToolErrorCode.PATH_DENIED
    assert invalid.error_code is ToolErrorCode.INVALID_ARGUMENTS
    assert unsupported.error_code is ToolErrorCode.INVALID_ARGUMENTS


def test_progressive_reads_report_actual_visible_ranges_after_truncation(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    source = "".join(f"VALUE_{index} = {'x' * 200}\n" for index in range(120))
    (root / "app" / "large.py").write_text(source, encoding="utf-8")
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    result = toolbox.execute(
        _call(
            "large-range",
            "read_range",
            {"path": "app/large.py", "start_line": 1, "end_line": 120},
        )
    )

    assert result.ok is True
    payload = json.loads(result.content)
    assert payload["requested_start_line"] == 1
    assert payload["requested_end_line"] == 120
    assert payload["returned_end_line"] < payload["requested_end_line"]
    assert payload["truncated"] is True

    symbol_source = "def huge():\n" + "".join(
        f"    item_{index} = {'x' * 200}\n" for index in range(120)
    )
    (root / "app" / "large.py").write_text(symbol_source, encoding="utf-8")
    symbol = toolbox.execute(
        _call("huge-symbol", "read_symbol", {"path": "app/large.py", "symbol": "huge"})
    )

    assert symbol.ok is True
    symbol_payload = json.loads(symbol.content)
    assert symbol_payload["requested_end_line"] > symbol_payload["returned_end_line"]
    assert symbol_payload["truncated"] is True


def test_repository_tools_treat_missing_listing_directory_as_empty(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    listed = toolbox.execute(_call("1", "list_files", {"directory": "new_package"}))

    assert listed.ok is True
    assert json.loads(listed.content) == {
        "files": [],
        "truncated": False,
        "directory_exists": False,
    }


def test_write_file_enforces_writable_and_readonly_scope_before_disk_mutation(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    allowed = toolbox.execute(
        _call("1", "write_file", {"path": "app/new.py", "content": "NEW = True\n"})
    )
    protected = toolbox.execute(
        _call(
            "2",
            "write_file",
            {"path": "tests/test_main.py", "content": "tampered\n"},
        )
    )
    out_of_scope = toolbox.execute(
        _call("3", "write_file", {"path": "docs/new.md", "content": "no\n"})
    )

    assert allowed.ok is True
    assert (root / "app" / "new.py").read_text(encoding="utf-8") == "NEW = True\n"

    assert protected.ok is False
    assert protected.error_code is ToolErrorCode.PATH_DENIED
    assert (root / "tests" / "test_main.py").read_text(encoding="utf-8") == (
        "def test_value(): pass\n"
    )

    assert out_of_scope.ok is False
    assert out_of_scope.error_code is ToolErrorCode.PATH_DENIED
    assert not (root / "docs" / "new.md").exists()


def test_repository_tools_never_expose_git_internals(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    read = toolbox.execute(_call("1", "read_file", {"path": ".git/config"}))
    write = toolbox.execute(
        _call("2", "write_file", {"path": ".git/config", "content": "tampered"})
    )

    assert read.ok is False
    assert read.error_code is ToolErrorCode.PATH_DENIED
    assert write.ok is False
    assert write.error_code is ToolErrorCode.PATH_DENIED


def test_apply_patch_requires_unique_exact_context(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    applied = toolbox.execute(
        _call(
            "1",
            "apply_patch",
            {"path": "app/main.py", "old_text": "VALUE = 1", "new_text": "VALUE = 2"},
        )
    )
    missing = toolbox.execute(
        _call(
            "2",
            "apply_patch",
            {"path": "app/main.py", "old_text": "VALUE = 9", "new_text": "VALUE = 3"},
        )
    )

    assert applied.ok is True
    assert (root / "app" / "main.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert missing.ok is False
    assert missing.error_code is ToolErrorCode.NOT_FOUND


def test_internal_symlink_cannot_redirect_writable_path_to_readonly_file(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    link = root / "app" / "linked_test.py"
    try:
        link.symlink_to(root / "tests" / "test_main.py")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows user does not have permission to create symbolic links")
        raise
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=_task())

    result = toolbox.execute(
        _call("1", "write_file", {"path": "app/linked_test.py", "content": "tampered\n"})
    )

    assert result.ok is False
    assert (root / "tests" / "test_main.py").read_text(encoding="utf-8") == (
        "def test_value(): pass\n"
    )


class FakeDriver:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeDriver received more calls than expected")
        return self.responses.pop(0)


class HangingAfterResponsesDriver(FakeDriver):
    async def complete(self, request):
        if self.responses:
            return await super().complete(request)
        await asyncio.Event().wait()


def _response(*, content: str = "", tool_calls: list[ToolCall] | None = None) -> AgentResponse:
    return AgentResponse(
        model="test/developer",
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        latency_ms=7,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def test_developer_agent_uses_tools_then_stops_without_declaring_runtime_success(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(tool_calls=[_call("read-1", "read_file", {"path": "app/main.py"})]),
            _response(
                tool_calls=[
                    _call(
                        "patch-1",
                        "apply_patch",
                        {
                            "path": "app/main.py",
                            "old_text": "VALUE = 1",
                            "new_text": "VALUE = 2",
                        },
                    )
                ]
            ),
            _response(content="Changed VALUE from 1 to 2; runtime verification is still required."),
        ]
    )
    developer = DeveloperAgent(driver=driver, model="test/developer")

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert result.iterations == 3
    assert result.tool_calls == 2
    assert result.changed_files == ["app/main.py"]
    assert result.usage.total_tokens == 45
    assert result.latency_ms == 21
    assert result.execution_budget is not None
    assert result.execution_budget.max_iterations == 12
    assert result.execution_budget.max_duration_seconds == 300.0
    assert result.execution_budget.max_model_turn_seconds == 120.0
    assert all(request.max_output_tokens == 1_400 for request in driver.requests)
    assert "Prefer tool calls over prose" in driver.requests[0].messages[0].content
    assert "exactly three concise items" in driver.requests[0].messages[0].content
    assert not hasattr(result, "passed")
    assert (root / "app" / "main.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    second_request = driver.requests[1]
    assert second_request.messages[-2].tool_calls[0].name == "read_file"
    assert second_request.messages[-1].role.value == "tool"
    assert second_request.messages[-1].tool_call_id == "read-1"
    assert {tool.name for tool in second_request.tools} == {
        "list_files",
        "read_file",
        "read_files",
        "read_range",
        "read_symbol",
        "search_code",
        "search_code_many",
        "write_file",
        "apply_patch",
    }


def test_developer_agent_is_bounded_by_iteration_budget(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(tool_calls=[_call("1", "list_files", {})]),
            _response(tool_calls=[_call("2", "list_files", {})]),
        ]
    )
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_iterations=2,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.ITERATION_LIMIT
    assert result.iterations == 2
    assert result.tool_calls == 2
    assert len(driver.requests) == 2


def test_repeated_invalid_write_file_stops_without_marking_budget_progress(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    invalid_call = ToolCall(id="bad-write", name="write_file", arguments="{")
    driver = FakeDriver(
        [
            _response(tool_calls=[invalid_call]),
            _response(tool_calls=[invalid_call.model_copy(update={"id": "bad-write-2"})]),
        ]
    )

    result = asyncio.run(
        DeveloperAgent(driver=driver, model="test/developer", max_iterations=6).run(
            _task(), workspace=workspace
        )
    )

    assert result.stop_reason is DeveloperStopReason.REPEATED_TOOL_FAILURE
    assert result.tool_calls == 2
    assert result.changed_files == []
    assert len(driver.requests) == 2
    assert driver.requests[1].budget_progress is False
    assert driver.requests[1].max_output_tokens == 3_200
    assert "complete JSON object" in driver.requests[1].messages[-1].content
    assert result.tool_failure_evidence == (
        "tool=write_file;error_code=INVALID_ARGUMENTS;arguments=invalid_json;fields=-",
    )

    failure = SingleTaskOrchestrator._developer_stop_failure(result)
    assert failure.failure_type.value == "INVALID_TOOL_ARGUMENTS"
    assert failure.source.value == "tool"
    assert failure.retryable is False
    assert result.tool_failure_evidence[0] in failure.evidence


def test_invalid_write_file_gets_one_larger_structured_retry(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    invalid_call = ToolCall(id="bad-write", name="write_file", arguments="{")
    driver = FakeDriver(
        [
            _response(tool_calls=[invalid_call]),
            _response(
                tool_calls=[
                    _call(
                        "fixed-write",
                        "write_file",
                        {"path": "app/game.py", "content": "def run():\n    return 'ok'\n"},
                    )
                ]
            ),
            _response(content="已修改文件: app/game.py\n已执行验证: 无\n遗留事项: 无"),
        ]
    )

    result = asyncio.run(
        DeveloperAgent(
            driver=driver,
            model="test/developer",
            max_iterations=6,
            max_output_tokens=1_400,
            invalid_tool_retry_max_output_tokens=3_200,
        ).run(_task(), workspace=workspace)
    )

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3
    assert driver.requests[0].max_output_tokens == 1_400
    assert driver.requests[1].max_output_tokens == 3_200
    assert "small runnable skeleton" in driver.requests[1].messages[-1].content
    assert driver.requests[2].max_output_tokens == 1_400
    # Successful source writes are represented by the bounded working-state digest
    # rather than replaying the full write_file arguments into the next model turn.
    assert all(
        not message.tool_calls
        or all(call.id != "fixed-write" for call in message.tool_calls)
        for message in driver.requests[2].messages
    )
    assert any(
        message.role is MessageRole.USER
        and "changed_files" in message.content
        and "app/game.py" in message.content
        for message in driver.requests[2].messages
    )
    assert (root / "app" / "game.py").is_file()


def test_developer_uses_one_tool_recovery_liveness_credit_through_budgeted_driver(
    tmp_path: Path,
) -> None:
    class RecordingBudgetStore:
        def __init__(self) -> None:
            self.credits: list[LivenessCredit] = []

        async def reserve(self, **kwargs):  # type: ignore[no-untyped-def]
            self.credits.append(kwargs["liveness_credit"])
            return object()

        async def settle(self, _reservation, _usage):  # type: ignore[no-untyped-def]
            return None

        async def cancel(self, _reservation):  # type: ignore[no-untyped-def]
            return None

    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    invalid_call = ToolCall(id="bad-write", name="write_file", arguments="{")
    provider = FakeDriver(
        [
            _response(tool_calls=[invalid_call]),
            _response(
                tool_calls=[
                    _call(
                        "fixed-write",
                        "write_file",
                        {"path": "app/game.py", "content": "def run():\n    return 'ok'\n"},
                    )
                ]
            ),
            _response(content="已修改文件: app/game.py\n已执行验证: 无\n遗留事项: 无"),
        ]
    )
    budget_store = RecordingBudgetStore()
    developer = DeveloperAgent(
        driver=BudgetedAgentDriver(
            driver=provider,
            budget_store=budget_store,  # type: ignore[arg-type]
            run_id=uuid4(),
            task_id="DEV-001",
        ),
        model="test/developer",
        max_iterations=6,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert budget_store.credits == [
        LivenessCredit.INITIAL_STARTUP,
        LivenessCredit.TOOL_RECOVERY,
        LivenessCredit.VERIFIED_PROGRESS,
    ]


def test_different_invalid_argument_shapes_do_not_stop_as_repeated_failure(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(tool_calls=[ToolCall(id="invalid-json", name="write_file", arguments="{")]),
            _response(
                tool_calls=[
                    _call(
                        "missing-content",
                        "write_file",
                        {"path": "app/game.py"},
                    )
                ]
            ),
            _response(content="已修改文件: 无\n已执行验证: 无\n遗留事项: 需要补充文件"),
        ]
    )

    result = asyncio.run(
        DeveloperAgent(driver=driver, model="test/developer", max_iterations=6).run(
            _task(), workspace=workspace
        )
    )

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert len(driver.requests) == 3


def test_developer_compacts_old_complete_tool_groups_without_orphans(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(tool_calls=[_call("read-1", "read_file", {"path": "app/main.py"})]),
            _response(
                tool_calls=[
                    _call(
                        "read-2",
                        "read_range",
                        {"path": "app/main.py", "start_line": 1, "end_line": 1},
                    )
                ]
            ),
            _response(
                tool_calls=[
                    _call("read-3", "read_symbol", {"path": "app/main.py", "symbol": "VALUE"})
                ]
            ),
            _response(content="No code changes required."),
        ]
    )
    developer = DeveloperAgent(driver=driver, model="test/developer", max_iterations=4)

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    final_request = driver.requests[3]
    tool_messages = [message for message in final_request.messages if message.role.value == "tool"]
    assistant_calls = [
        message
        for message in final_request.messages
        if message.role.value == "assistant" and message.tool_calls
    ]
    assert [message.tool_call_id for message in tool_messages] == ["read-3"]
    assert [message.tool_calls[0].id for message in assistant_calls] == ["read-3"]
    assert any("compact working state" in message.content for message in final_request.messages)


def test_developer_retains_only_one_of_many_tool_groups_with_bounded_digest(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    responses = [
        _response(tool_calls=[_call(f"read-{index}", "read_file", {"path": "app/main.py"})])
        for index in range(1, 12)
    ] + [_response(content="done")]
    driver = FakeDriver(responses)

    result = asyncio.run(
        DeveloperAgent(driver=driver, model="test/developer", max_iterations=12).run(
            _task(), workspace=workspace
        )
    )

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    final_request = driver.requests[-1]
    tool_messages = [
        message for message in final_request.messages if message.role is MessageRole.TOOL
    ]
    assistant_messages = [
        message
        for message in final_request.messages
        if message.role is MessageRole.ASSISTANT and message.tool_calls
    ]
    assert [item.tool_call_id for item in tool_messages] == ["read-11"]
    assert [item.tool_calls[0].id for item in assistant_messages] == ["read-11"]
    digest_message = next(
        message for message in final_request.messages if "compact working state" in message.content
    )
    assert '"source_hash"' in digest_message.content
    assert len(final_request.messages) < 10


def test_developer_agent_verifies_scoped_changes_after_iteration_budget(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-1",
                        "write_file",
                        {"path": "app/main.py", "content": "VALUE = 2\n"},
                    )
                ]
            )
        ]
    )
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_iterations=1,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.ITERATION_LIMIT
    assert result.iterations == 1
    assert result.changed_files == ["app/main.py"]
    assert "iteration budget" in result.final_message


def test_developer_agent_is_bounded_by_time_budget_before_provider_call(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver([])
    times = iter([0.0, 2.0])
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_duration_seconds=1.0,
        clock=lambda: next(times),
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.TIME_LIMIT
    assert result.iterations == 0
    assert driver.requests == []
    assert result.execution_budget is not None
    assert result.execution_budget.max_duration_seconds == 1.0
    assert result.execution_budget.max_model_turn_seconds == 1.0


def test_developer_agent_continues_to_verification_when_final_turn_times_out_after_changes(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = HangingAfterResponsesDriver(
        [
            _response(
                tool_calls=[
                    _call(
                        "write-1",
                        "write_file",
                        {"path": "app/main.py", "content": "VALUE = 2\n"},
                    )
                ]
            )
        ]
    )
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_duration_seconds=3.0,
        max_model_turn_seconds=1.0,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.TIME_LIMIT
    assert result.iterations == 1
    assert result.changed_files == ["app/main.py"]
    assert "timed out" in result.final_message


def test_developer_agent_rejects_tool_call_fanout_before_execution(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(
                tool_calls=[
                    _call("1", "write_file", {"path": "app/a.py", "content": "A = 1\n"}),
                    _call("2", "write_file", {"path": "app/b.py", "content": "B = 1\n"}),
                ]
            )
        ]
    )
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        max_tool_calls_per_turn=1,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.TOOL_CALL_LIMIT
    assert result.tool_calls == 0
    assert not (root / "app" / "a.py").exists()
    assert not (root / "app" / "b.py").exists()

def test_developer_runtime_v3_observation_does_not_count_as_budget_progress(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    driver = FakeDriver(
        [
            _response(tool_calls=[_call("read-v3", "read_file", {"path": "app/main.py"})]),
            _response(
                tool_calls=[
                    _call(
                        "patch-v3",
                        "apply_patch",
                        {
                            "path": "app/main.py",
                            "old_text": "VALUE = 1",
                            "new_text": "VALUE = 2",
                        },
                    )
                ]
            ),
            _response(content="已修改文件: app/main.py\n已执行验证: 无\n遗留事项: 无"),
        ]
    )
    developer = DeveloperAgent(
        driver=driver,
        model="test/developer",
        runtime_v3_enabled=True,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert result.changed_files == ["app/main.py"]
    assert len(driver.requests) == 3
    assert driver.requests[0].budget_progress is False
    assert driver.requests[1].budget_progress is False
    assert driver.requests[2].budget_progress is True
    assert (root / "app" / "main.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_developer_runtime_v3_preserves_tool_recovery_liveness_credit(
    tmp_path: Path,
) -> None:
    class RecordingBudgetStore:
        def __init__(self) -> None:
            self.credits: list[LivenessCredit] = []

        async def reserve(self, **kwargs):  # type: ignore[no-untyped-def]
            self.credits.append(kwargs["liveness_credit"])
            return object()

        async def settle(self, _reservation, _usage):  # type: ignore[no-untyped-def]
            return None

        async def cancel(self, _reservation):  # type: ignore[no-untyped-def]
            return None

    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    invalid_call = ToolCall(id="bad-write-v3", name="write_file", arguments="{")
    provider = FakeDriver(
        [
            _response(tool_calls=[invalid_call]),
            _response(
                tool_calls=[
                    _call(
                        "fixed-write-v3",
                        "write_file",
                        {"path": "app/game.py", "content": "def run():\n    return 'ok'\n"},
                    )
                ]
            ),
            _response(content="已修改文件: app/game.py\n已执行验证: 无\n遗留事项: 无"),
        ]
    )
    budget_store = RecordingBudgetStore()
    developer = DeveloperAgent(
        driver=BudgetedAgentDriver(
            driver=provider,
            budget_store=budget_store,  # type: ignore[arg-type]
            run_id=uuid4(),
            task_id="DEV-001",
        ),
        model="test/developer",
        max_iterations=6,
        runtime_v3_enabled=True,
    )

    result = asyncio.run(developer.run(_task(), workspace=workspace))

    assert result.stop_reason is DeveloperStopReason.MODEL_STOP
    assert budget_store.credits == [
        LivenessCredit.INITIAL_STARTUP,
        LivenessCredit.TOOL_RECOVERY,
        LivenessCredit.VERIFIED_PROGRESS,
    ]
    assert provider.requests[1].max_output_tokens == 3_200
    assert "complete JSON object" in provider.requests[1].messages[-1].content


def test_developer_runtime_v3_repeated_invalid_tool_call_is_terminal(
    tmp_path: Path,
) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    invalid_call = ToolCall(id="bad-v3-1", name="write_file", arguments="{")
    driver = FakeDriver(
        [
            _response(tool_calls=[invalid_call]),
            _response(tool_calls=[invalid_call.model_copy(update={"id": "bad-v3-2"})]),
        ]
    )

    result = asyncio.run(
        DeveloperAgent(
            driver=driver,
            model="test/developer",
            max_iterations=6,
            runtime_v3_enabled=True,
        ).run(_task(), workspace=workspace)
    )

    assert result.stop_reason is DeveloperStopReason.REPEATED_TOOL_FAILURE
    assert result.changed_files == []
    assert result.tool_failure_evidence == (
        "tool=write_file;error_code=INVALID_ARGUMENTS;arguments=invalid_json;fields=-",
    )
