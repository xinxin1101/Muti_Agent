from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from app.api.failure_explanation import FailureExplanationService
from app.api.models import ProductRunFailure
from app.models.agent import AgentResponse, TokenUsage


class _Driver:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        assert "__pycache__" in request.messages[1].content
        assert request.max_output_tokens == 400
        assert "不超过 300 字" in request.messages[0].content
        return AgentResponse(
            model="test-model",
            content="验证容器的项目目录只读，Python 无法创建 __pycache__。请修复环境后重试。",
            usage=TokenUsage(),
            latency_ms=1,
        )


class _Cache:
    def __init__(self) -> None:
        self.values = {}

    async def load(self, run_id, fingerprint):
        return self.values.get((run_id, fingerprint))

    async def save(self, *, run_id, fingerprint, model, explanation):
        created_at = datetime(2026, 8, 26, tzinfo=UTC)
        self.values[(run_id, fingerprint)] = (explanation, model, created_at)
        return created_at

    async def dispose(self):
        return None


def test_failure_explanation_uses_only_structured_evidence_and_caches_result() -> None:
    async def scenario() -> None:
        run_id = uuid4()
        driver = _Driver()
        service = FailureExplanationService(driver=driver, model="test-model", cache=_Cache())
        failures = (
            ProductRunFailure(
                task_id="hello-world-python",
                failure_type="TOOL_FAILURE",
                source="verification",
                message="Verification command could not write bytecode cache.",
                retryable=False,
                evidence=("command=python3 -m py_compile hello.py", "stderr=__pycache__"),
            ),
        )

        first = await service.explain(run_id=run_id, failures=failures)
        second = await service.explain(run_id=run_id, failures=failures)

        assert not first.cached
        assert second.cached
        assert first.failure_fingerprint == second.failure_fingerprint
        assert driver.calls == 1

    asyncio.run(scenario())
