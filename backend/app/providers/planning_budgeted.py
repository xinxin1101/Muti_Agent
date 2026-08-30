from __future__ import annotations

from uuid import UUID

from app.models.agent import AgentRequest, AgentResponse
from app.persistence.planning_budget import (
    PlanningTokenBudgetReservationError,
    PostgresPlanningTokenBudgetStore,
)
from app.providers.base import AgentDriver
from app.providers.errors import AgentProviderError, ProviderErrorCode


class PlanningBudgetedAgentDriver:
    """Budget the Planner before the Run and its normal token budget exist."""

    def __init__(
        self,
        *,
        driver: AgentDriver,
        budget_store: PostgresPlanningTokenBudgetStore,
        launch_id: UUID,
    ) -> None:
        self._driver = driver
        self._budget_store = budget_store
        self._launch_id = launch_id

    async def complete(self, request: AgentRequest) -> AgentResponse:
        estimate = max(request.context_estimated_tokens, self._estimate_request_tokens(request))
        try:
            reservation = await self._budget_store.reserve(
                launch_id=self._launch_id,
                estimated_input_tokens=estimate,
                max_output_tokens=request.max_output_tokens,
            )
        except PlanningTokenBudgetReservationError as exc:
            raise AgentProviderError(
                provider="devflow-planning-budget",
                code=ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED,
                message=str(exc),
                retryable=False,
            ) from exc
        try:
            response = await self._driver.complete(request)
        except BaseException:
            await self._budget_store.cancel(reservation)
            raise
        await self._budget_store.settle(reservation, response.usage)
        return response

    @staticmethod
    def _estimate_request_tokens(request: AgentRequest) -> int:
        chars = sum(len(message.content) for message in request.messages)
        chars += sum(len(tool.name) + len(tool.description) for tool in request.tools)
        return max(1, (chars + 2) // 3)
