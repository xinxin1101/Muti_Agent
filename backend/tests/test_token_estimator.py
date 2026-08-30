from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentMessage, AgentRequest, AgentRole, MessageRole
from app.models.tools import ToolCall, ToolDefinition


def test_context_window_units_are_not_reused_as_billable_token_reservation() -> None:
    estimator = TokenEstimator()
    source = "x" * 32_000

    assert estimator.context_window_units(source) == 32_000
    assert estimator.billable_token_estimate(source) < 12_000


def test_billable_estimate_handles_cjk_code_and_mixed_content() -> None:
    estimator = TokenEstimator()

    chinese = estimator.billable_token_estimate("实现五子棋游戏" * 20)
    code = estimator.billable_token_estimate("def hello():\n    return 'world'\n" * 20)
    mixed = estimator.billable_token_estimate("实现 hello world: print('你好')" * 20)

    assert chinese > 0
    assert code > 0
    assert mixed > 0
    assert mixed < estimator.context_window_units("实现 hello world: print('你好')" * 20)


def test_agent_request_estimate_includes_large_tool_arguments_and_schema() -> None:
    estimator = TokenEstimator()
    base = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/model",
        messages=[AgentMessage(role=MessageRole.USER, content="implement")],
    )
    patch = "x" * 20_000
    complete = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/model",
        messages=[
            AgentMessage(
                role=MessageRole.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="call-large", name="write_file", arguments=patch)],
            ),
            AgentMessage(role=MessageRole.TOOL, tool_call_id="call-large", content="ok"),
        ],
        tools=[
            ToolDefinition(
                name="write_file",
                description="write a file",
                parameters={"type": "object", "properties": {"content": {"maxLength": 200000}}},
            )
        ],
    )

    assert (
        estimator.estimate_agent_request(complete)
        > estimator.estimate_agent_request(base) + 5_000
    )
