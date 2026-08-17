import asyncio
from types import SimpleNamespace

from app.models import (
    AgentMessage,
    AgentRequest,
    AgentRole,
    MessageRole,
    ToolCall,
    ToolDefinition,
)
from app.providers import SiliconFlowDriver


class FakeCompletions:
    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _read_tool() -> ToolDefinition:
    return ToolDefinition(
        name="read_file",
        description="Read one repository file.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )


def test_siliconflow_driver_normalizes_native_function_call() -> None:
    raw_tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="read_file", arguments='{"path":"app/main.py"}'),
    )
    response = SimpleNamespace(
        model="test/developer",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None, tool_calls=[raw_tool_call]),
                finish_reason="tool_calls",
            )
        ],
        usage=None,
    )
    completions = FakeCompletions(response)
    driver = SiliconFlowDriver(client=FakeClient(completions))
    request = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/developer",
        messages=[AgentMessage(role=MessageRole.USER, content="Inspect the file")],
        tools=[_read_tool()],
    )

    result = asyncio.run(driver.complete(request))

    assert result.content == ""
    assert result.tool_calls == [
        ToolCall(id="call-1", name="read_file", arguments='{"path":"app/main.py"}')
    ]
    assert completions.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read one repository file.",
                "parameters": _read_tool().parameters,
            },
        }
    ]


def test_siliconflow_driver_serializes_assistant_tool_call_and_tool_observation() -> None:
    response = SimpleNamespace(
        model="test/developer",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )
    completions = FakeCompletions(response)
    driver = SiliconFlowDriver(client=FakeClient(completions))
    call = ToolCall(id="call-1", name="read_file", arguments='{"path":"app/main.py"}')
    request = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/developer",
        messages=[
            AgentMessage(role=MessageRole.USER, content="Inspect the file"),
            AgentMessage(role=MessageRole.ASSISTANT, tool_calls=[call]),
            AgentMessage(
                role=MessageRole.TOOL,
                content='{"ok":true,"content":"VALUE = 1"}',
                tool_call_id="call-1",
            ),
        ],
        tools=[_read_tool()],
    )

    result = asyncio.run(driver.complete(request))

    assert result.content == "done"
    assert completions.calls[0]["messages"] == [
        {"role": "user", "content": "Inspect the file"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"app/main.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": '{"ok":true,"content":"VALUE = 1"}',
            "tool_call_id": "call-1",
        },
    ]
