from __future__ import annotations

import asyncio
from uuid import uuid4

from app.models.agent import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentRole,
    MessageRole,
    TokenUsage,
)
from app.providers.base import AgentDriver
from app.providers.budgeted import BudgetedAgentDriver


class _Provider(AgentDriver):
    async def complete(self, request: AgentRequest) -> AgentResponse:
        del request
        return AgentResponse(
            model="test/model",
            content="candidate observation",
            usage=TokenUsage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
            latency_ms=1,
            finish_reason="stop",
        )


class _BudgetStore:
    def __init__(self) -> None:
        self.reserve_progress: bool | None = None
        self.model_turn_progress: bool | None = None
        self.tool_turn_progress: bool | None = None

    async def reserve(self, **kwargs):
        self.reserve_progress = kwargs["has_progress"]
        return object()

    async def settle(self, reservation, usage) -> None:
        del reservation, usage

    async def cancel(self, reservation) -> None:
        del reservation

    async def record_model_turn_observation(self, **kwargs) -> int:
        self.model_turn_progress = kwargs["has_real_progress"]
        return 7

    async def record_tool_outcome_observation(self, **kwargs) -> None:
        self.tool_turn_progress = kwargs["has_real_progress"]


def test_budget_admission_uses_previous_progress_but_audit_waits_for_current_turn() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Provider(),
        budget_store=store,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-1",
    )
    request = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/model",
        messages=[AgentMessage(role=MessageRole.USER, content="continue")],
        max_output_tokens=100,
        budget_progress=True,
        execution_iteration=2,
    )

    asyncio.run(driver.complete(request))
    asyncio.run(
        driver.record_tool_outcome(
            role=AgentRole.DEVELOPER.value,
            calls=[],
            results=[],
            has_real_progress=False,
        )
    )

    assert store.reserve_progress is True
    assert store.model_turn_progress is False
    assert store.tool_turn_progress is False


def test_current_tool_progress_is_written_by_tool_outcome_not_request_credit() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Provider(),
        budget_store=store,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-1",
    )
    request = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="test/model",
        messages=[AgentMessage(role=MessageRole.USER, content="continue")],
        max_output_tokens=100,
        budget_progress=False,
        execution_iteration=1,
    )

    asyncio.run(driver.complete(request))
    asyncio.run(
        driver.record_tool_outcome(
            role=AgentRole.DEVELOPER.value,
            calls=[],
            results=[],
            has_real_progress=True,
        )
    )

    assert store.reserve_progress is False
    assert store.model_turn_progress is False
    assert store.tool_turn_progress is True
