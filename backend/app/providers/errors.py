import asyncio
from enum import StrEnum

import openai

from app.models.failure import FailureReport, FailureSource, FailureType


class ProviderErrorCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    BAD_REQUEST = "bad_request"
    CONNECTION = "connection"
    UNAVAILABLE = "unavailable"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    UNKNOWN = "unknown"


class AgentProviderError(RuntimeError):
    """Normalized provider failure exposed to the DevFlow runtime."""

    def __init__(
        self,
        *,
        provider: str,
        code: ProviderErrorCode,
        message: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.status_code = status_code

    def to_failure_report(self) -> FailureReport:
        if self.code is ProviderErrorCode.TIMEOUT:
            failure_type = FailureType.MODEL_TIMEOUT
        elif self.code is ProviderErrorCode.RATE_LIMIT:
            failure_type = FailureType.RATE_LIMIT
        elif self.code is ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED:
            failure_type = FailureType.TOKEN_BUDGET_EXHAUSTED
        else:
            failure_type = FailureType.TOOL_FAILURE

        evidence = [f"provider={self.provider}", f"code={self.code.value}"]
        if self.status_code is not None:
            evidence.append(f"status_code={self.status_code}")

        return FailureReport(
            failure_type=failure_type,
            source=FailureSource.PROVIDER,
            message=str(self),
            retryable=self.retryable,
            evidence=evidence,
        )


def normalize_provider_error(exc: Exception, *, provider: str) -> AgentProviderError:
    """Map SDK/network failures into stable DevFlow provider error semantics."""

    status_code = getattr(exc, "status_code", None)

    if isinstance(exc, (openai.APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.TIMEOUT,
            message="Model request timed out.",
            retryable=True,
            status_code=status_code,
        )

    if isinstance(exc, openai.RateLimitError) or status_code == 429:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.RATE_LIMIT,
            message="Provider rate limit was reached.",
            retryable=True,
            status_code=429,
        )

    if isinstance(exc, openai.AuthenticationError) or status_code == 401:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.AUTHENTICATION,
            message="Provider authentication failed.",
            retryable=False,
            status_code=401,
        )

    if isinstance(exc, openai.PermissionDeniedError) or status_code == 403:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.PERMISSION,
            message="Provider permission was denied.",
            retryable=False,
            status_code=403,
        )

    if isinstance(exc, (openai.BadRequestError, openai.NotFoundError)) or status_code in {
        400,
        404,
        422,
    }:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.BAD_REQUEST,
            message="Provider rejected the model request.",
            retryable=False,
            status_code=status_code,
        )

    if isinstance(exc, openai.APIConnectionError):
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.CONNECTION,
            message="Provider connection failed.",
            retryable=True,
            status_code=status_code,
        )

    if isinstance(exc, openai.InternalServerError) or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.UNAVAILABLE,
            message="Provider service is temporarily unavailable.",
            retryable=True,
            status_code=status_code,
        )

    return AgentProviderError(
        provider=provider,
        code=ProviderErrorCode.UNKNOWN,
        message="Unexpected provider failure.",
        retryable=False,
        status_code=status_code,
    )
