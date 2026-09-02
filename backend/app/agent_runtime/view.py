from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.context.retention import AgentContextRetention
from app.models.agent import AgentMessage, MessageRole


class AgentView(BaseModel):
    """Provider-facing view separated from the complete runtime/evidence history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[AgentMessage, ...] = Field(min_length=1)
    compacted_tool_groups: int = Field(default=0, ge=0)


class AgentViewBuilder:
    @staticmethod
    def build(
        retention: AgentContextRetention,
        *,
        runtime_instruction: str | None = None,
    ) -> AgentView:
        messages = retention.messages()
        if runtime_instruction:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER,
                    content=runtime_instruction,
                )
            )
        return AgentView(
            messages=tuple(messages),
            compacted_tool_groups=retention.compacted_group_count,
        )
