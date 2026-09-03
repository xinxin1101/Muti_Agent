from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.types import ToolProgressKind


class AgentRuntimeEventKind(StrEnum):
    MODEL_RESPONSE = "MODEL_RESPONSE"
    TOOL_RESULT = "TOOL_RESULT"
    MUTATION_GATE = "MUTATION_GATE"
    PREFETCH = "PREFETCH"
    STUCK_DETECTION = "STUCK_DETECTION"


class AgentRuntimeEvent(BaseModel):
    """Bounded runtime metadata. Raw source/tool payloads remain outside this event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    kind: AgentRuntimeEventKind
    iteration: int = Field(ge=0)
    tool_name: str | None = Field(default=None, max_length=128)
    ok: bool | None = None
    progress_kind: ToolProgressKind | None = None
    detail: str = Field(default="", max_length=500)