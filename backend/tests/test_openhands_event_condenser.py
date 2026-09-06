from __future__ import annotations

import json

from app.agent_runtime.condenser import AgentCondenser
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


def _assistant(*calls: ToolCall) -> AgentMessage:
    return AgentMessage(role=MessageRole.ASSISTANT, tool_calls=calls)


def _read_group(index: int) -> tuple[ToolCall, ToolExecutionResult]:
    call = ToolCall(
        id=f"read-{index}",
        name="read_file",
        arguments=json.dumps({"path": f"module_{index}.py"}),
    )
    result = ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=True,
        content=json.dumps(
            {"path": f"module_{index}.py", "content": f"value = {index}"}
        ),
    )
    return call, result


def test_event_condenser_uses_append_only_events_and_bounded_view() -> None:
    condenser = AgentCondenser(
        task_id="event-loop",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=2,
        max_single_tool_result_tokens=1_200,
        max_tool_results_per_turn_tokens=2_400,
        event_native_enabled=True,
    )

    for index in range(12):
        call, result = _read_group(index)
        condenser.add_group(
            assistant=_assistant(call),
            calls=[call],
            results=[result],
        )

    state = condenser.state()

    assert state.compacted_tool_groups == 10
    assert state.condensation_count == 10
    assert state.event_count == 22
    assert len(state.messages) == 6
    assert "DevFlow compact working state" in state.messages[1].content
    assert [message.role for message in state.messages[-4:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]


def test_successful_mutation_is_condensed_without_replaying_source() -> None:
    source = "x" * 8_000
    call = ToolCall(
        id="write",
        name="write_file",
        arguments=json.dumps({"path": "gomoku/core.py", "content": source}),
    )
    result = ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=True,
        content=json.dumps({"path": "gomoku/core.py", "written": True}),
    )
    condenser = AgentCondenser(
        task_id="mutation",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=2,
        max_single_tool_result_tokens=1_200,
        max_tool_results_per_turn_tokens=2_400,
        event_native_enabled=True,
    )

    compacted_current_group = condenser.add_group(
        assistant=_assistant(call),
        calls=[call],
        results=[result],
    )
    state = condenser.state()
    text = "\n".join(message.content for message in state.messages)

    assert compacted_current_group is True
    assert state.event_count == 2
    assert state.condensation_count == 1
    assert state.compacted_tool_groups == 1
    assert source[:1_000] not in text
    assert "gomoku/core.py" in text
    assert [message.role for message in state.messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
    ]


def test_openhands_multi_file_patch_paths_survive_condensation_summary() -> None:
    patch = """*** Begin Patch
*** Update File: src/a.py
@@
-old
+new
*** Add File: src/b.py
+value = 1
*** End Patch"""
    call = ToolCall(
        id="patch",
        name="apply_patch",
        arguments=json.dumps({"patch": patch}),
    )
    result = ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=True,
        content=json.dumps(
            {
                "engine": "openhands",
                "paths": ["src/a.py", "src/b.py"],
                "operations": ["update:src/a.py", "add:src/b.py"],
                "fuzz": 0,
            }
        ),
    )
    condenser = AgentCondenser(
        task_id="patch-paths",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=1,
        max_single_tool_result_tokens=1_200,
        max_tool_results_per_turn_tokens=2_400,
        event_native_enabled=True,
    )

    condenser.add_group(assistant=_assistant(call), calls=[call], results=[result])
    summary = condenser.state().messages[-1].content

    assert "src/a.py" in summary
    assert "src/b.py" in summary
    assert patch not in summary


def test_event_condenser_preserves_valid_tool_call_pairing_in_view() -> None:
    condenser = AgentCondenser(
        task_id="pairing",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=1,
        max_single_tool_result_tokens=1_200,
        max_tool_results_per_turn_tokens=2_400,
        event_native_enabled=True,
    )
    first_call, first_result = _read_group(1)
    second_call, second_result = _read_group(2)

    condenser.add_group(
        assistant=_assistant(first_call),
        calls=[first_call],
        results=[first_result],
    )
    condenser.add_group(
        assistant=_assistant(second_call),
        calls=[second_call],
        results=[second_result],
    )
    messages = condenser.state().messages

    assert [message.role for message in messages[-2:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    assert messages[-2].tool_calls[0].id == messages[-1].tool_call_id == second_call.id


def test_legacy_retention_remains_an_explicit_rollback_path() -> None:
    condenser = AgentCondenser(
        task_id="rollback",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=1,
        max_single_tool_result_tokens=1_200,
        max_tool_results_per_turn_tokens=2_400,
        event_native_enabled=False,
    )
    first_call, first_result = _read_group(1)
    second_call, second_result = _read_group(2)

    condenser.add_group(
        assistant=_assistant(first_call),
        calls=[first_call],
        results=[first_result],
    )
    condenser.add_group(
        assistant=_assistant(second_call),
        calls=[second_call],
        results=[second_result],
    )
    state = condenser.state()

    assert state.compacted_tool_groups == 1
    assert state.condensation_count == 0
    assert state.event_count == 0
    assert "DevFlow compact working state" in state.messages[1].content