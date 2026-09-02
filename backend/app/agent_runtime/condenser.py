from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.context.retention import AgentContextRetention
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


class CondensedAgentState(BaseModel):
    """Bounded provider-facing working state derived from retained runtime evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[AgentMessage, ...] = Field(min_length=1)
    compacted_tool_groups: int = Field(default=0, ge=0)


class AgentCondenser:
    """Compatibility condenser hiding the current retention implementation from AgentLoop.

    Raw tool/source payload retention remains bounded by AgentContextRetention for now. The shared
    runtime consumes only this working-state interface, so a later event-native condenser can
    replace the retention backend without changing Developer/Repair loop semantics.
    """

    def __init__(
        self,
        *,
        task_id: str,
        base_messages: list[AgentMessage],
        max_retained_tool_groups: int,
        max_single_tool_result_tokens: int,
        max_tool_results_per_turn_tokens: int,
    ) -> None:
        self._retention = AgentContextRetention(
            task_id=task_id,
            base_messages=base_messages,
            max_retained_tool_groups=max_retained_tool_groups,
            max_single_tool_result_tokens=max_single_tool_result_tokens,
            max_tool_results_per_turn_tokens=max_tool_results_per_turn_tokens,
        )

    def state(self, *, runtime_instruction: str | None = None) -> CondensedAgentState:
        messages = self._retention.messages()
        if runtime_instruction:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER,
                    content=runtime_instruction,
                )
            )
        return CondensedAgentState(
            messages=tuple(messages),
            compacted_tool_groups=self._retention.compacted_group_count,
        )

    def add_group(
        self,
        *,
        assistant: AgentMessage,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> bool:
        return self._retention.add_group(
            assistant=assistant,
            calls=calls,
            results=results,
        )
