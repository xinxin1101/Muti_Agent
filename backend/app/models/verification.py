from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.failure import FailureType


class CheckType(StrEnum):
    SCOPE = "scope"
    TEST = "test"
    LINT = "lint"
    TYPE_CHECK = "type_check"
    BUILD = "build"
    CUSTOM = "custom"


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_type: CheckType
    name: str = Field(min_length=1, max_length=256)
    command: str | None = None
    passed: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = Field(default=0, ge=0)
    failure_type: FailureType | None = None

    @model_validator(mode="after")
    def validate_check_consistency(self) -> CheckResult:
        if self.passed and self.exit_code not in (None, 0):
            raise ValueError("passed checks cannot have a non-zero exit code")
        if self.passed and self.failure_type is not None:
            raise ValueError("passed checks cannot have a failure_type")
        if not self.passed and self.failure_type is None:
            raise ValueError("failed checks require a failure_type")
        return self


class VerificationResult(BaseModel):
    """Deterministic hard-gate evidence for one task attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checks: list[CheckResult] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_aggregate_result(self) -> VerificationResult:
        expected = all(check.passed for check in self.checks)
        if self.passed is not expected:
            raise ValueError("verification passed flag must match all check results")
        return self
