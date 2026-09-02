import asyncio
import hashlib
import json
import re
from enum import StrEnum
from typing import Any

import openai

from app.models.failure import FailureReport, FailureSource, FailureType


class ProviderErrorCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    BAD_REQUEST = "bad_request"
    INVALID_REQUEST = "invalid_request"
    CONNECTION = "connection"
    UNAVAILABLE = "unavailable"
    TOKEN_BUDGET_EXHAUSTED = "token_budget_exhausted"
    WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED = "work_package_budget_allocation_blocked"
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
        evidence: list[str] | None = None,
        failure_source: FailureSource | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.evidence = tuple(evidence or ())
        self.failure_source = failure_source

    def to_failure_report(self) -> FailureReport:
        if self.code is ProviderErrorCode.TIMEOUT:
            failure_type = FailureType.MODEL_TIMEOUT
        elif self.code is ProviderErrorCode.RATE_LIMIT:
            failure_type = FailureType.RATE_LIMIT
        elif self.code is ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED:
            failure_type = FailureType.TOKEN_BUDGET_EXHAUSTED
        elif self.code is ProviderErrorCode.WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED:
            failure_type = FailureType.WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED
        elif self.code in {
            ProviderErrorCode.BAD_REQUEST,
            ProviderErrorCode.INVALID_REQUEST,
        }:
            failure_type = FailureType.PROVIDER_REQUEST_REJECTED
        else:
            failure_type = FailureType.TOOL_FAILURE

        evidence = [f"provider={self.provider}", f"code={self.code.value}", *self.evidence]
        if self.status_code is not None:
            evidence.append(f"status_code={self.status_code}")

        return FailureReport(
            failure_type=failure_type,
            source=self.failure_source or (
                FailureSource.RUNTIME
                if self.code is ProviderErrorCode.WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED
                else FailureSource.PROVIDER
            ),
            message=str(self),
            retryable=self.retryable,
            evidence=evidence,
        )


_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|token|secret|password)\b\s*[:=]\s*[^,;\s]+"
)
_PROVIDER_ERROR_KEYS = ("code", "type", "param", "message")


def _sanitize_provider_text(value: object, *, limit: int = 500) -> str:
    text = " ".join(str(value).split())
    text = _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}\b", "sk-<redacted>", text)
    text = re.sub(
        r'(["\'])([^"\']{121,})\1',
        lambda match: f"{match.group(1)}<redacted-long-value>{match.group(1)}",
        text,
    )
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _provider_error_payload(exc: Exception) -> tuple[dict[str, str], str | None]:
    body = getattr(exc, "body", None)
    body_hash: str | None = None
    if body is not None:
        try:
            serialized = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            serialized = str(body)
        body_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    candidate: Any = body
    if isinstance(candidate, dict) and isinstance(candidate.get("error"), dict):
        candidate = candidate["error"]

    fields: dict[str, str] = {}
    if isinstance(candidate, dict):
        for key in _PROVIDER_ERROR_KEYS:
            value = candidate.get(key)
            if value is not None:
                fields[key] = _sanitize_provider_text(value)

    request_id = getattr(exc, "request_id", None)
    if not request_id:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            request_id = headers.get("x-request-id") or headers.get("x-requestid")
    if request_id:
        fields["request_id"] = _sanitize_provider_text(request_id, limit=200)
    return fields, body_hash


def _provider_error_evidence(
    exc: Exception,
    *,
    request_evidence: list[str] | None,
) -> list[str]:
    evidence = list(request_evidence or ())
    fields, body_hash = _provider_error_payload(exc)
    for key in _PROVIDER_ERROR_KEYS:
        value = fields.get(key)
        if value:
            evidence.append(f"provider_error_{key}={value}")
    if fields.get("request_id"):
        evidence.append(f"provider_request_id={fields['request_id']}")
    if body_hash is not None:
        evidence.append(f"provider_error_body_sha256={body_hash}")
    return evidence


def normalize_provider_error(
    exc: Exception,
    *,
    provider: str,
    request_evidence: list[str] | None = None,
) -> AgentProviderError:
    """Map SDK/network failures into stable, sanitized DevFlow provider error semantics."""

    status_code = getattr(exc, "status_code", None)
    evidence = _provider_error_evidence(exc, request_evidence=request_evidence)

    if isinstance(exc, (openai.APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.TIMEOUT,
            message="Model request timed out.",
            retryable=True,
            status_code=status_code,
            evidence=evidence,
        )

    if isinstance(exc, openai.RateLimitError) or status_code == 429:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.RATE_LIMIT,
            message="Provider rate limit was reached.",
            retryable=True,
            status_code=429,
            evidence=evidence,
        )

    if isinstance(exc, openai.AuthenticationError) or status_code == 401:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.AUTHENTICATION,
            message="Provider authentication failed.",
            retryable=False,
            status_code=401,
            evidence=evidence,
        )

    if isinstance(exc, openai.PermissionDeniedError) or status_code == 403:
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.PERMISSION,
            message="Provider permission was denied.",
            retryable=False,
            status_code=403,
            evidence=evidence,
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
            evidence=evidence,
        )

    if isinstance(exc, openai.APIConnectionError):
        return AgentProviderError(
            provider=provider,
            code=ProviderErrorCode.CONNECTION,
            message="Provider connection failed.",
            retryable=True,
            status_code=status_code,
            evidence=evidence,
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
            evidence=evidence,
        )

    return AgentProviderError(
        provider=provider,
        code=ProviderErrorCode.UNKNOWN,
        message="Unexpected provider failure.",
        retryable=False,
        status_code=status_code,
        evidence=evidence,
    )
