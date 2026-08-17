from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import TokenUsage
from app.models.failure import FailureType


class RepairStopReason(StrEnum):
    MODEL_STOP = "MODEL_STOP"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"


class RepairRunResult(BaseModel):
    """Evidence from one targeted repair attempt; this is not a success verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1)
    failure_types: list[FailureType] = Field(min_length=1)
    stop_reason: RepairStopReason
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(default=0, ge=0)
