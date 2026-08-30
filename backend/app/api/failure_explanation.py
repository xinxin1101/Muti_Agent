from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.api.models import ProductFailureExplanation, ProductRunFailure
from app.models.agent import AgentMessage, AgentRequest, AgentRole, MessageRole
from app.providers.base import AgentDriver

_MAX_EXPLANATION_CHARS = 2_000
_MODEL_TIMEOUT_SECONDS = 30.0


class FailureExplanationCache(Protocol):
    async def load(self, run_id: UUID, fingerprint: str) -> tuple[str, str, datetime] | None: ...

    async def save(
        self,
        *,
        run_id: UUID,
        fingerprint: str,
        model: str,
        explanation: str,
    ) -> datetime: ...

    async def dispose(self) -> None: ...


class FailureExplanationUnavailableError(RuntimeError):
    """The optional explanation layer cannot run; durable diagnostics remain available."""


class FailureExplanationService:
    """Translate already-accepted, redacted failure facts into optional Chinese guidance."""

    def __init__(
        self,
        *,
        driver: AgentDriver | None,
        model: str,
        cache: FailureExplanationCache,
        max_output_tokens: int = 400,
        enable_thinking: bool = False,
    ) -> None:
        self._driver = driver
        self._model = model
        self._cache = cache
        if not 64 <= max_output_tokens <= 32_768:
            raise ValueError("max_output_tokens must be between 64 and 32768")
        self._max_output_tokens = max_output_tokens
        self._enable_thinking = enable_thinking

    async def dispose(self) -> None:
        await self._cache.dispose()

    async def explain(
        self,
        *,
        run_id: UUID,
        failures: tuple[ProductRunFailure, ...],
    ) -> ProductFailureExplanation:
        if not failures:
            raise ValueError("the failed Run has no structured failure evidence to explain")
        fingerprint = self._fingerprint(failures)
        cached = await self._cache.load(run_id, fingerprint)
        if cached is not None:
            explanation, model, created_at = cached
            return ProductFailureExplanation(
                run_id=run_id,
                failure_fingerprint=fingerprint,
                explanation=explanation,
                model=model,
                cached=True,
                created_at=created_at,
            )
        if self._driver is None:
            raise FailureExplanationUnavailableError(
                "AI 解读未配置模型服务，请先配置 SiliconFlow 密钥。"
            )

        request = AgentRequest(
            role=AgentRole.REVIEWER,
            model=self._model,
            temperature=0.1,
            max_output_tokens=self._max_output_tokens,
            enable_thinking=self._enable_thinking,
            messages=[
                AgentMessage(
                    role=MessageRole.SYSTEM,
                    content=(
                        "你是 DevFlow 的故障说明助手。只能依据用户提供的结构化失败证据，"
                        "用简体中文写一段不超过 300 字的清晰纯文本说明，按“发生了什么；最可能原因；"
                        "建议下一步”顺序组织。不要使用 Markdown、标题标记、加粗标记或重复证据。"
                        "不得声称已执行未出现的操作；不得要求或泄露密钥、令牌、"
                        "完整源码。说明仅用于辅助理解，不改变系统的失败判定或重试权限。"
                    ),
                ),
                AgentMessage(
                    role=MessageRole.USER,
                    content="以下是已脱敏且受长度限制的失败证据：\n"
                    + json.dumps(
                        [item.model_dump(mode="json") for item in failures],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            ],
        )
        try:
            async with asyncio.timeout(_MODEL_TIMEOUT_SECONDS):
                response = await self._driver.complete(request)
        except TimeoutError as exc:
            raise FailureExplanationUnavailableError("AI 解读请求超时，请稍后重试。") from exc
        except Exception as exc:
            raise FailureExplanationUnavailableError("AI 解读暂时不可用，请稍后重试。") from exc
        explanation = response.content.strip()[:_MAX_EXPLANATION_CHARS]
        if not explanation:
            raise FailureExplanationUnavailableError("AI 解读未返回有效内容，请稍后重试。")
        created_at = await self._cache.save(
            run_id=run_id,
            fingerprint=fingerprint,
            model=response.model,
            explanation=explanation,
        )
        return ProductFailureExplanation(
            run_id=run_id,
            failure_fingerprint=fingerprint,
            explanation=explanation,
            model=response.model,
            cached=False,
            created_at=created_at,
        )

    @staticmethod
    def _fingerprint(failures: tuple[ProductRunFailure, ...]) -> str:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in failures],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
