from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReviewOutcome(StrEnum):
    PASS = "PASS"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"


class ReviewSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: ReviewSeverity
    message: str = Field(min_length=1, max_length=4000)
    file: str | None = None
    line: int | None = Field(default=None, ge=1)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review issue message must not be empty")
        return normalized


class ReviewDecision(BaseModel):
    """Schema-validated semantic review result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ReviewOutcome
    summary: str = Field(min_length=1, max_length=4000)
    issues: list[ReviewIssue] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("review summary must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> ReviewDecision:
        if self.decision is ReviewOutcome.PASS and self.issues:
            raise ValueError("PASS review decisions must not contain issues")
        if self.decision is ReviewOutcome.CHANGES_REQUESTED and not self.issues:
            raise ValueError("CHANGES_REQUESTED review decisions require at least one issue")
        return self
