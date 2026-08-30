from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TaskContinuationSummary(BaseModel):
    """Bounded, durable facts for one automatic checkpoint continuation chain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slices_started: int = Field(ge=1, le=20)
    max_slices: int = Field(ge=1, le=20)
    total_budget_seconds: float = Field(gt=0, le=86_400)
    elapsed_ms: int = Field(ge=0)
    complexity_score: int = Field(default=0, ge=0, le=16)
    resumed_from_commits: tuple[str, ...] = Field(default_factory=tuple, max_length=19)
    stop_reason: str = Field(min_length=1, max_length=128)
