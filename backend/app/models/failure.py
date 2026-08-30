from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FailureType(StrEnum):
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    AGENT_TIME_LIMIT = "AGENT_TIME_LIMIT"
    RATE_LIMIT = "RATE_LIMIT"
    INVALID_AGENT_OUTPUT = "INVALID_AGENT_OUTPUT"
    TOOL_FAILURE = "TOOL_FAILURE"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    TEST_FAILURE = "TEST_FAILURE"
    LINT_FAILURE = "LINT_FAILURE"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    SANDBOX_TIMEOUT = "SANDBOX_TIMEOUT"
    VERIFICATION_ENV_UNAVAILABLE = "VERIFICATION_ENV_UNAVAILABLE"
    TOKEN_BUDGET_EXHAUSTED = "TOKEN_BUDGET_EXHAUSTED"
    INTERFACE_CONTRACT_UNMET = "INTERFACE_CONTRACT_UNMET"


class FailureSource(StrEnum):
    PROVIDER = "provider"
    TOOL = "tool"
    VERIFICATION = "verification"
    REVIEW = "review"
    RUNTIME = "runtime"


class FailureReport(BaseModel):
    """Normalized failure evidence used for bounded retry or terminal failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_type: FailureType
    source: FailureSource
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool
    evidence: list[str] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("failure message must not be empty")
        return normalized

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("failure evidence entries must not be empty")
        return normalized
