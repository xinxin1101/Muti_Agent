from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.context.token_estimator import TokenEstimator
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
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._driver = driver
        self._budget_store = budget_store
        self._launch_id = launch_id
        self._token_estimator = token_estimator or TokenEstimator()

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

    async def ensure_capacity(self, requests: Sequence[AgentRequest]) -> None:
        """Fail closed before the first Planner call when one bounded recovery cannot fit."""

        if not requests:
            raise ValueError("planning capacity requires at least one request")
        required_tokens = sum(
            max(request.context_estimated_tokens, self._estimate_request_tokens(request))
            + request.max_output_tokens
            for request in requests
        )
        try:
            await self._budget_store.ensure_capacity(
                launch_id=self._launch_id,
                required_tokens=required_tokens,
                required_calls=len(requests),
            )
        except PlanningTokenBudgetReservationError as exc:
            raise AgentProviderError(
                provider="devflow-planning-budget",
                code=ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED,
                message=str(exc),
                retryable=False,
            ) from exc

    def _estimate_request_tokens(self, request: AgentRequest) -> int:
        return self._token_estimator.estimate_agent_request(request)
