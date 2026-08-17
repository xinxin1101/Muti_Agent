from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ToolCall(BaseModel):
    """Provider-neutral function call requested by an LLM."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    arguments: str = Field(default="{}")

    @field_validator("id", "name")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class ToolDefinition(BaseModel):
    """JSON-Schema function definition exposed to a model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, Any]

    @field_validator("name", "description")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class ToolErrorCode(StrEnum):
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    PATH_DENIED = "PATH_DENIED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS_PATCH = "AMBIGUOUS_PATCH"
    IO_ERROR = "IO_ERROR"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"


class ToolExecutionResult(BaseModel):
    """Structured observation returned to the Developer Agent after one tool call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    ok: bool
    content: str = Field(default="")
    error_code: ToolErrorCode | None = None

    @field_validator("tool_call_id", "name")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized
