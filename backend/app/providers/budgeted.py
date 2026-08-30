from __future__ import annotations

from uuid import UUID

from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentRequest, AgentResponse
from app.persistence.token_budget import PostgresRunTokenBudgetStore, TokenBudgetReservationError
from app.providers.base import AgentDriver
from app.providers.errors import AgentProviderError, ProviderErrorCode


class BudgetedAgentDriver:
    """Reserve a durable Run budget before the wrapped driver can contact a provider."""

    def __init__(
        self,
        *,
        driver: AgentDriver,
        budget_store: PostgresRunTokenBudgetStore,
        run_id: UUID,
        task_id: str,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._driver = driver
        self._budget_store = budget_store
        self._run_id = run_id
        self._task_id = task_id
        self._token_estimator = token_estimator or TokenEstimator()

    async def complete(self, request: AgentRequest) -> AgentResponse:
        estimated_input = max(
            request.context_estimated_tokens,
            self._estimate_request_tokens(request),
        )
        try:
            reservation = await self._budget_store.reserve(
                run_id=self._run_id,
                task_id=self._task_id,
                role=request.role,
                estimated_input_tokens=estimated_input,
                max_output_tokens=request.max_output_tokens,
                has_progress=request.budget_progress,
            )
        except TokenBudgetReservationError as exc:
            raise AgentProviderError(
                provider="devflow-token-budget",
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

    def _estimate_request_tokens(self, request: AgentRequest) -> int:
        return self._token_estimator.estimate_agent_request(request)
