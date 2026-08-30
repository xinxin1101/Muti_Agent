from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.tools import ToolCall, ToolDefinition


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
    content: str = Field(default="")
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        return value.strip()

    @field_validator("tool_call_id")
    @classmethod
    def normalize_tool_call_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("tool_call_id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_role_payload(self) -> "AgentMessage":
        if self.role in {MessageRole.SYSTEM, MessageRole.USER}:
            if not self.content:
                raise ValueError("system and user messages require content")
            if self.tool_call_id is not None or self.tool_calls:
                raise ValueError("system and user messages cannot contain tool metadata")
            return self

        if self.role is MessageRole.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("assistant messages cannot contain tool_call_id")
            if not self.content and not self.tool_calls:
                raise ValueError("assistant messages require content or tool_calls")
            return self

        if self.role is MessageRole.TOOL:
            if self.tool_call_id is None:
                raise ValueError("tool messages require tool_call_id")
            if not self.content:
                raise ValueError("tool messages require content")
            if self.tool_calls:
                raise ValueError("tool messages cannot contain nested tool_calls")
        return self


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
    # This is deliberately provider-neutral. Drivers translate it to their native completion
    # limit (SiliconFlow/OpenAI-compatible APIs use ``max_tokens``).
    max_output_tokens: int = Field(default=1024, ge=64, le=32_768)
    # Mixed-reasoning providers such as DashScope Qwen otherwise enable hidden reasoning by
    # default. This is explicit so each persisted execution path can be cost-audited.
    enable_thinking: bool = False
    # Set by bounded agent loops after a successful tool action or code change.  It is
    # deliberately a runtime fact rather than a model claim and gates FLEX borrowing.
    budget_progress: bool = False
    # Used by DevFlow's local budget gate and deliberately never forwarded to a provider.
    context_estimated_tokens: int = Field(default=0, ge=0)
    tools: list[ToolDefinition] = Field(default_factory=list)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_unique_tools(self) -> "AgentRequest":
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tool definitions must have unique names")
        return self


class AgentResponse(BaseModel):
    """Provider-neutral normalized completion result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    content: str = Field(default="")
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(ge=0)
    finish_reason: str | None = None

    @model_validator(mode="after")
    def validate_completion_payload(self) -> "AgentResponse":
        if not self.content and not self.tool_calls:
            raise ValueError("agent response requires content or tool_calls")
        return self
