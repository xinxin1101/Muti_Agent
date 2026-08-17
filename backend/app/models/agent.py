from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AgentRole(StrEnum):
    PLANNER = "planner"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    REPAIR = "repair"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("message content must not be empty")
        return normalized


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class AgentRequest(BaseModel):
    """Provider-neutral request used by an agent role to invoke an LLM driver."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    model: str = Field(min_length=1, max_length=256)
    messages: list[AgentMessage] = Field(min_length=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be empty")
        return normalized


class AgentResponse(BaseModel):
    """Provider-neutral normalized completion result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    content: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = None
