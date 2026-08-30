from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.context import ContextContinuationState


class CheckpointReason(StrEnum):
    """Why a bounded task execution preserved an internal continuation point."""

    TIME_LIMIT = "TIME_LIMIT"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"


class TaskCheckpoint(BaseModel):
    """Immutable Git continuation point created only by the fenced worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_files: tuple[str, ...] = Field(min_length=1, max_length=512)
    reason: CheckpointReason
    summary: str = Field(min_length=1, max_length=512)
    slice_index: int = Field(default=1, ge=1, le=20)
    max_slices: int = Field(default=1, ge=1, le=20)
    elapsed_ms: int = Field(default=0, ge=0)
    resume_from_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    completed_summary: str = Field(default="", max_length=512)
    remaining_summary: str = Field(default="", max_length=512)
    verification_summary: str = Field(default="", max_length=512)
    failure_summary: str = Field(default="", max_length=512)
    completed_interfaces: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    remaining_interfaces: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    remaining_budget_tokens: int | None = Field(default=None, ge=0)
    context_state: ContextContinuationState | None = None
