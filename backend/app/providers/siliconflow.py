import asyncio
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.core.settings import Settings
from app.models.agent import AgentMessage, AgentRequest, AgentResponse, MessageRole, TokenUsage
from app.models.failure import FailureSource
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
        proxy_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if not 0 <= max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.proxy_url = proxy_url.rstrip("/") if proxy_url else None
        self._owns_client = client is None

        if client is not None:
            self._client = client
            return

        normalized_key = self._secret_value(api_key)
        if not normalized_key:
            raise ValueError("SiliconFlow API key is required when no client is injected")

        client_options: dict[str, Any] = {
            "api_key": normalized_key,
            "base_url": self.base_url,
            "timeout": timeout_seconds,
            # DevFlow owns retry classification/backoff so SDK retries cannot double-submit.
            "max_retries": 0,
        }
        if self.proxy_url is not None:
            # Keep proxy routing inside the provider client. It must not leak into Agent tools or
            # disconnected verification containers.
            client_options["http_client"] = httpx.AsyncClient(proxy=self.proxy_url)
        self._client = AsyncOpenAI(**client_options)

    @classmethod
    def from_settings(cls, settings: Settings, *, client: Any | None = None) -> "SiliconFlowDriver":
        return cls(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            timeout_seconds=settings.siliconflow_timeout_seconds,
            max_retries=settings.siliconflow_max_retries,
            proxy_url=settings.siliconflow_proxy_url,
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
        request_evidence = self._safe_request_evidence(request)
        self._validate_provider_request(request, request_evidence=request_evidence)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self._serialize_message(message) for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": False,
        }
        if request.tools:
            payload["tools"] = [self._serialize_tool(tool) for tool in request.tools]
        # DashScope Qwen mixed-thinking models default to reasoning on. Keep the control
        # provider-specific so other OpenAI-compatible services do not receive an unknown field.
        if "dashscope.aliyuncs.com" in self.base_url:
            payload["extra_body"] = {"enable_thinking": request.enable_thinking}

        completion = await self._create_completion_with_retry(
            payload,
            request_evidence=request_evidence,
        )

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

    async def _create_completion_with_retry(
        self,
        payload: dict[str, Any],
        *,
        request_evidence: list[str],
    ) -> Any:
        """Retry only normalized transient provider failures within a small bounded budget."""

        for retry_index in range(self.max_retries + 1):
            try:
                return await self._client.chat.completions.create(**payload)
            except Exception as exc:
                error = normalize_provider_error(
                    exc,
                    provider=self.provider_name,
                    request_evidence=request_evidence,
                )
                if not error.retryable or retry_index >= self.max_retries:
                    attempts = retry_index + 1
                    if attempts > 1:
                        error = self._with_retry_evidence(error, attempts=attempts)
                    raise error from exc
                await asyncio.sleep(self._retry_delay_seconds(exc, retry_index=retry_index))

        raise RuntimeError("unreachable provider retry state")

    @staticmethod
    def _retry_delay_seconds(exc: Exception, *, retry_index: int) -> float:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            raw_retry_after = headers.get("retry-after") or headers.get("Retry-After")
            if raw_retry_after is not None:
                try:
                    return min(5.0, max(0.0, float(raw_retry_after)))
                except (TypeError, ValueError):
                    pass
        return min(2.0, 0.25 * (2**retry_index))

    @staticmethod
    def _with_retry_evidence(
        error: AgentProviderError,
        *,
        attempts: int,
    ) -> AgentProviderError:
        return AgentProviderError(
            provider=error.provider,
            code=error.code,
            message=str(error),
            retryable=error.retryable,
            status_code=error.status_code,
            evidence=[
                *error.evidence,
                f"provider_attempts={attempts}",
                f"provider_retries={attempts - 1}",
            ],
            failure_source=error.failure_source,
        )

    def _safe_request_evidence(self, request: AgentRequest) -> list[str]:
        roles = ",".join(message.role.value for message in request.messages)
        assistant_tool_calls = sum(
            len(message.tool_calls)
            for message in request.messages
            if message.role is MessageRole.ASSISTANT
        )
        tool_messages = sum(
            1 for message in request.messages if message.role is MessageRole.TOOL
        )
        host = urlparse(self.base_url).netloc or "unknown"
        evidence = [
            f"request_role={request.role.value}",
            f"request_model={request.model}",
            f"request_iteration={request.execution_iteration}",
            f"request_message_count={len(request.messages)}",
            f"request_message_roles={roles}",
            f"request_tool_definition_count={len(request.tools)}",
            f"request_assistant_tool_call_count={assistant_tool_calls}",
            f"request_tool_message_count={tool_messages}",
            f"request_enable_thinking={str(request.enable_thinking).lower()}",
            f"provider_base_host={host}",
        ]
        if request.tools:
            evidence.append(
                "request_tool_names=" + ",".join(tool.name for tool in request.tools)
            )
        return evidence

    def _validate_provider_request(
        self,
        request: AgentRequest,
        *,
        request_evidence: list[str],
    ) -> None:
        tool_names = {tool.name for tool in request.tools}
        for tool in request.tools:
            schema_type = tool.parameters.get("type")
            if schema_type not in {None, "object"}:
                raise self._invalid_request(
                    "tool_schema_root_not_object",
                    request_evidence=request_evidence,
                    detail=f"tool={tool.name};schema_type={schema_type}",
                )

        pending_tool_call_ids: set[str] = set()
        seen_tool_call_ids: set[str] = set()
        for index, message in enumerate(request.messages):
            if pending_tool_call_ids and message.role is not MessageRole.TOOL:
                raise self._invalid_request(
                    "tool_results_not_contiguous",
                    request_evidence=request_evidence,
                    detail=(
                        f"message_index={index};pending_tool_results="
                        f"{len(pending_tool_call_ids)}"
                    ),
                )

            if message.role is MessageRole.ASSISTANT and message.tool_calls:
                call_ids = [call.id for call in message.tool_calls]
                if len(call_ids) != len(set(call_ids)):
                    raise self._invalid_request(
                        "duplicate_tool_call_id_in_message",
                        request_evidence=request_evidence,
                        detail=f"message_index={index}",
                    )
                duplicate_history_ids = set(call_ids) & seen_tool_call_ids
                if duplicate_history_ids:
                    raise self._invalid_request(
                        "duplicate_tool_call_id_in_history",
                        request_evidence=request_evidence,
                        detail=f"message_index={index}",
                    )
                unknown_tools = sorted(
                    {call.name for call in message.tool_calls if call.name not in tool_names}
                )
                if unknown_tools:
                    raise self._invalid_request(
                        "assistant_references_unadvertised_tool",
                        request_evidence=request_evidence,
                        detail="tool_count=" + str(len(unknown_tools)),
                    )
                pending_tool_call_ids = set(call_ids)
                seen_tool_call_ids.update(call_ids)
                continue

            if message.role is MessageRole.TOOL:
                tool_call_id = message.tool_call_id
                if not pending_tool_call_ids:
                    raise self._invalid_request(
                        "orphan_tool_message",
                        request_evidence=request_evidence,
                        detail=f"message_index={index}",
                    )
                if tool_call_id not in pending_tool_call_ids:
                    raise self._invalid_request(
                        "tool_call_id_mismatch",
                        request_evidence=request_evidence,
                        detail=f"message_index={index}",
                    )
                pending_tool_call_ids.remove(tool_call_id)

        if pending_tool_call_ids:
            raise self._invalid_request(
                "missing_tool_results",
                request_evidence=request_evidence,
                detail=f"pending_tool_results={len(pending_tool_call_ids)}",
            )

    @staticmethod
    def _invalid_request(
        validation_code: str,
        *,
        request_evidence: list[str],
        detail: str,
    ) -> AgentProviderError:
        return AgentProviderError(
            provider=SiliconFlowDriver.provider_name,
            code=ProviderErrorCode.INVALID_REQUEST,
            message="Provider request blocked by local structural validation.",
            retryable=False,
            evidence=[
                *request_evidence,
                "validation_stage=local_preflight",
                f"validation_code={validation_code}",
                detail,
                "provider_called=false",
            ],
            failure_source=FailureSource.RUNTIME,
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
