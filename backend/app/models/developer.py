from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import TokenUsage


class DeveloperStopReason(StrEnum):
    MODEL_STOP = "MODEL_STOP"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"
    REPEATED_TOOL_FAILURE = "REPEATED_TOOL_FAILURE"


class DeveloperExecutionBudget(BaseModel):
    """The effective bounded-execution configuration for one Developer run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_iterations: int = Field(ge=1, le=20)
    max_duration_seconds: float = Field(ge=1.0, le=600.0)
    max_model_turn_seconds: float = Field(ge=1.0, le=600.0)


class DeveloperRunResult(BaseModel):
    """Execution evidence from Developer Agent work; this is not a success verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_reason: DeveloperStopReason
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(default=0, ge=0)
    # Older persisted runs predate this field. New production runs always record it so a
    # terminal budget stop can be diagnosed against the configuration the Worker actually used.
    execution_budget: DeveloperExecutionBudget | None = None
    # Safe, bounded diagnostics only: tool name, error code and argument shape. Raw tool
    # arguments and repository content are deliberately never persisted here.
    tool_failure_evidence: tuple[str, ...] = Field(default=(), max_length=8)
