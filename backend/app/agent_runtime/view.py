from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.condenser import AgentCondenser
from app.models.agent import AgentMessage


class AgentView(BaseModel):
    """Provider-facing view separated from the complete runtime/evidence history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[AgentMessage, ...] = Field(min_length=1)
    compacted_tool_groups: int = Field(default=0, ge=0)


class AgentViewBuilder:
    @staticmethod
    def build(
        condenser: AgentCondenser,
        *,
        runtime_instruction: str | None = None,
    ) -> AgentView:
        state = condenser.state(runtime_instruction=runtime_instruction)
        return AgentView(
            messages=state.messages,
            compacted_tool_groups=state.compacted_tool_groups,
        )
