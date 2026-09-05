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


class ReviewerClosureContext(BaseModel):
    """Bounded evidence connecting a prior rejected review to the next closure review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_round: int = Field(ge=2, le=100)
    previous_decision: ReviewDecision
    repair_attempt_start: int = Field(ge=1, le=100)
    repair_attempt_end: int = Field(ge=1, le=100)
    repair_changed_files: tuple[str, ...] = Field(min_length=1, max_length=64)
    patch_hash_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_hash_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    repair_delta: str = Field(min_length=1, max_length=30_000)

    @field_validator("repair_changed_files")
    @classmethod
    def normalize_changed_files(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(path.strip() for path in value if path.strip())
        if not normalized:
            raise ValueError("reviewer closure context requires changed files")
        if len(normalized) != len(set(normalized)):
            raise ValueError("reviewer closure changed files must be unique")
        return normalized

    @field_validator("repair_delta")
    @classmethod
    def normalize_repair_delta(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reviewer closure repair delta must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_closure_consistency(self) -> "ReviewerClosureContext":
        if self.previous_decision.decision is not ReviewOutcome.CHANGES_REQUESTED:
            raise ValueError("reviewer closure requires a prior CHANGES_REQUESTED decision")
        if self.repair_attempt_end < self.repair_attempt_start:
            raise ValueError("repair attempt end must be greater than or equal to start")
        if self.patch_hash_before == self.patch_hash_after:
            raise ValueError("reviewer closure requires a real workspace mutation")
        return self
