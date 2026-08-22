from time import perf_counter
from typing import Any

from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.settings import Settings
from app.models.agent import AgentMessage, AgentRequest, AgentResponse, MessageRole, TokenUsage
from app.models.tools import ToolCall, ToolDefinition
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
        self._owns_client = client is None

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

    async def dispose(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
            return
        aclose = getattr(self._client, "aclose", None)
        if aclose is not None:
            await aclose()

    async def list_model_ids(self) -> frozenset[str]:
        """Return the provider-advertised model ids for readiness checks.

        Model catalogue membership is operational evidence only; it never authorizes Run success,
        verification, integration, or fallback to a different model.
        """

        try:
            response = await self._client.models.list()
        except Exception as exc:
            raise normalize_provider_error(exc, provider=self.provider_name) from exc

        data = getattr(response, "data", None)
        if data is None:
            raise self._malformed_response("Provider model catalogue did not contain a data list.")
        model_ids: set[str] = set()
        for item in data:
            model_id = getattr(item, "id", None)
            if isinstance(model_id, str) and model_id.strip():
                model_ids.add(model_id.strip())
        if not model_ids:
            raise self._malformed_response("Provider model catalogue contained no model ids.")
        return frozenset(model_ids)

    async def complete(self, request: AgentRequest) -> AgentResponse:
        started_at = perf_counter()
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self._serialize_message(message) for message in request.messages],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [self._serialize_tool(tool) for tool in request.tools]

        try:
            completion = await self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise normalize_provider_error(exc, provider=self.provider_name) from exc

        latency_ms = max(0, int((perf_counter() - started_at) * 1000))
        choices = getattr(completion, "choices", None)
        if not choices:
            raise self._malformed_response("Provider response did not contain a completion choice.")

        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise self._malformed_response(
                "Provider response did not contain a completion message."
            )

        content = getattr(message, "content", None) or ""
        tool_calls = self._normalize_tool_calls(getattr(message, "tool_calls", None))
        if not content and not tool_calls:
            raise self._malformed_response(
                "Provider response contained neither assistant content nor tool calls."
            )

        usage = self._normalize_usage(getattr(completion, "usage", None))
        response_model = getattr(completion, "model", None) or request.model

        return AgentResponse(
            model=str(response_model),
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=getattr(choice, "finish_reason", None),
        )

    @staticmethod
    def _serialize_message(message: AgentMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role.value, "content": message.content}

        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            payload["content"] = message.content or None
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        elif message.role is MessageRole.TOOL:
            payload["tool_call_id"] = message.tool_call_id

        return payload

    @staticmethod
    def _serialize_tool(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    @classmethod
    def _normalize_tool_calls(cls, raw_calls: Any | None) -> list[ToolCall]:
        if not raw_calls:
            return []

        normalized: list[ToolCall] = []
        for raw_call in raw_calls:
            function = getattr(raw_call, "function", None)
            call_id = getattr(raw_call, "id", None)
            name = getattr(function, "name", None) if function is not None else None
            arguments = getattr(function, "arguments", None) if function is not None else None
            if not call_id or not name or arguments is None:
                raise cls._malformed_response("Provider returned a malformed function tool call.")
            normalized.append(ToolCall(id=str(call_id), name=str(name), arguments=str(arguments)))
        return normalized

    @staticmethod
    def _malformed_response(message: str) -> AgentProviderError:
        return AgentProviderError(
            provider=SiliconFlowDriver.provider_name,
            code=ProviderErrorCode.UNKNOWN,
            message=message,
            retryable=False,
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
