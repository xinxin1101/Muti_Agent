from app.context.retention import AgentContextRetention
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


def _assistant(*calls: ToolCall) -> AgentMessage:
    return AgentMessage(role=MessageRole.ASSISTANT, tool_calls=calls)


def test_retention_bounds_multiple_large_tool_results_per_turn() -> None:
    retention = AgentContextRetention(
        task_id="context-budget",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_single_tool_result_tokens=128,
        max_tool_results_per_turn_tokens=256,
    )
    calls = [
        ToolCall(id="first", name="read_file", arguments='{"path":"a.py"}'),
        ToolCall(id="second", name="read_file", arguments='{"path":"b.py"}'),
        ToolCall(id="third", name="read_file", arguments='{"path":"c.py"}'),
    ]
    results = [
        ToolExecutionResult(tool_call_id=call.id, name=call.name, ok=True, content="x" * 10_000)
        for call in calls
    ]

    assert retention.add_group(assistant=_assistant(*calls), calls=calls, results=results) is True
    messages = retention.messages()
    tool_messages = [message for message in messages if message.role is MessageRole.TOOL]

    assert tool_messages == []
    assert retention.tool_result_truncation_count == 3
    assert "DevFlow compact working state" in messages[-1].content


def test_successful_code_mutation_is_replaced_by_metadata_not_source() -> None:
    retention = AgentContextRetention(
        task_id="context-mutation",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
    )
    call = ToolCall(
        id="write",
        name="write_file",
        arguments='{"path":"gomoku/core.py","content":"' + "x" * 8_000 + '"}',
    )
    result = ToolExecutionResult(
        tool_call_id=call.id,
        name=call.name,
        ok=True,
        content='{"path":"gomoku/core.py","written":true}',
    )

    assert retention.add_group(assistant=_assistant(call), calls=[call], results=[result]) is True
    message_text = "\n".join(message.content for message in retention.messages())

    assert "x" * 1_000 not in message_text
    assert "gomoku/core.py" in message_text


def test_long_tool_loop_compacts_to_a_constant_number_of_messages() -> None:
    retention = AgentContextRetention(
        task_id="long-loop",
        base_messages=[AgentMessage(role=MessageRole.SYSTEM, content="system")],
        max_retained_tool_groups=2,
    )
    for index in range(12):
        call = ToolCall(
            id=f"read-{index}", name="read_file", arguments=f'{{"path":"module_{index}.py"}}'
        )
        result = ToolExecutionResult(
            tool_call_id=call.id,
            name=call.name,
            ok=True,
            content=f'{{"path":"module_{index}.py","content":"value = {index}"}}',
        )
        retention.add_group(assistant=_assistant(call), calls=[call], results=[result])

    messages = retention.messages()

    # system + compact state + two valid assistant/tool groups; no orphan tool messages.
    assert len(messages) == 6
    assert retention.compacted_group_count == 10
    assert [message.role for message in messages[-4:]] == [
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
