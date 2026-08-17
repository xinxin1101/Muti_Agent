from time import perf_counter
from typing import Any

from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.settings import Settings
from app.models.agent import AgentRequest, AgentResponse, TokenUsage
from app.providers.errors import AgentProviderError, ProviderErrorCode, normalize_provider_error


class SiliconFlowDriver:
    """OpenAI-compatible SiliconFlow implementation of the AgentDriver contract."""

    provider_name = "siliconflow"

    def __init__(
        self,
        *,
        api_key: str | SecretStr | None = None,
        base_url: str = "https://api.siliconflow.cn/v1",
        timeout_seconds: float = 60.0,
        max_retries: int = 0,
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

        if client is not None:
            self._client = client
            return

        normalized_key = self._secret_value(api_key)
        if not normalized_key:
            raise ValueError("SiliconFlow API key is required when no client is injected")

        self._client = AsyncOpenAI(
            api_key=normalized_key,
            base_url=self.base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, client: Any | None = None) -> "SiliconFlowDriver":
        return cls(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            timeout_seconds=settings.siliconflow_timeout_seconds,
            max_retries=settings.siliconflow_max_retries,
            client=client,
        )

    async def complete(self, request: AgentRequest) -> AgentResponse:
        started_at = perf_counter()

        try:
            completion = await self._client.chat.completions.create(
                model=request.model,
                messages=[
                    {"role": message.role.value, "content": message.content}
                    for message in request.messages
                ],
                temperature=request.temperature,
                stream=False,
            )
        except Exception as exc:
            raise normalize_provider_error(exc, provider=self.provider_name) from exc

        latency_ms = max(0, int((perf_counter() - started_at) * 1000))
        choices = getattr(completion, "choices", None)
        if not choices:
            raise AgentProviderError(
                provider=self.provider_name,
                code=ProviderErrorCode.UNKNOWN,
                message="Provider response did not contain a completion choice.",
                retryable=False,
            )

        choice = choices[0]
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None

        usage = self._normalize_usage(getattr(completion, "usage", None))
        response_model = getattr(completion, "model", None) or request.model

        return AgentResponse(
            model=str(response_model),
            content=content or "",
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=getattr(choice, "finish_reason", None),
        )

    @staticmethod
    def _secret_value(api_key: str | SecretStr | None) -> str | None:
        if api_key is None:
            return None
        value = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_usage(raw_usage: Any | None) -> TokenUsage:
        if raw_usage is None:
            return TokenUsage()

        prompt_tokens = int(getattr(raw_usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(raw_usage, "completion_tokens", 0) or 0)
        raw_total = getattr(raw_usage, "total_tokens", None)
        total_tokens = (
            int(raw_total) if raw_total is not None else prompt_tokens + completion_tokens
        )

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
