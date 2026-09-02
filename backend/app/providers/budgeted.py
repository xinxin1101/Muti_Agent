from __future__ import annotations

from uuid import UUID

from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentRequest, AgentResponse, LivenessCredit
from app.models.failure import FailureSource
from app.models.tools import ToolExecutionResult
from app.persistence.token_budget import (
    PostgresRunTokenBudgetStore,
    RunBudgetExhaustedError,
    TokenBudgetReservationError,
    WorkPackageBudgetAllocationError,
)
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
        self._latest_observation_ids: dict[str, int] = {}
        self._last_max_output_tokens: dict[str, int] = {}

    async def complete(self, request: AgentRequest) -> AgentResponse:
        request_breakdown = self._token_estimator.estimate_agent_request_breakdown(request)
        request_estimate = request_breakdown["request_total_tokens"]
        estimated_input = max(
            request.context_estimated_tokens,
            request_estimate,
        )
        # DeveloperAgent supplies the most specific value. This fallback keeps the
        # budget boundary safe for direct callers and older execution paths.
        liveness_credit = request.liveness_credit
        if liveness_credit is LivenessCredit.NORMAL:
            if request.execution_iteration == 1:
                liveness_credit = LivenessCredit.INITIAL_STARTUP
            elif request.budget_progress:
                liveness_credit = LivenessCredit.VERIFIED_PROGRESS
        try:
            reservation = await self._budget_store.reserve(
                run_id=self._run_id,
                task_id=self._task_id,
                role=request.role,
                estimated_input_tokens=estimated_input,
                max_output_tokens=request.max_output_tokens,
                has_progress=request.budget_progress,
                liveness_credit=liveness_credit,
            )
        except WorkPackageBudgetAllocationError as exc:
            raise AgentProviderError(
                provider="devflow-budget-policy",
                code=ProviderErrorCode.WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED,
                message=str(exc),
                # Recovery creates a new Run from the persisted checkpoint/DAG;
                # retrying the Repair Agent in this Run cannot add budget.
                retryable=False,
            ) from exc
        except TokenBudgetReservationError as exc:
            facts = exc.facts if isinstance(exc, RunBudgetExhaustedError) else None
            evidence = facts.evidence() if facts is not None else ["provider_called=false"]
            if facts is not None:
                evidence.extend(
                    [
                        (
                            "prompt_estimate_source=context_floor"
                            if request.context_estimated_tokens >= request_estimate
                            else "prompt_estimate_source=request_payload"
                        ),
                        f"prompt_context_floor_tokens={request.context_estimated_tokens}",
                        f"prompt_request_payload_tokens={request_estimate}",
                        f"prompt_metadata_tokens={request_breakdown['metadata_tokens']}",
                        f"prompt_message_tokens={request_breakdown['message_tokens']}",
                        (
                            "prompt_historical_tool_call_tokens="
                            f"{request_breakdown['historical_tool_call_tokens']}"
                        ),
                        (
                            "prompt_tool_definition_tokens="
                            f"{request_breakdown['tool_definition_tokens']}"
                        ),
                    ]
                )
            raise AgentProviderError(
                provider="runtime-budget",
                code=ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED,
                message=str(exc),
                retryable=False,
                evidence=evidence,
                failure_source=FailureSource.RUNTIME,
            ) from exc
        try:
            response = await self._driver.complete(request)
        except BaseException:
            await self._budget_store.cancel(reservation)
            raise
        await self._budget_store.settle(reservation, response.usage)
        record_observation = getattr(self._budget_store, "record_model_turn_observation", None)
        observation_id = (
            None
            if not callable(record_observation)
            else await record_observation(
                run_id=self._run_id,
                task_id=self._task_id,
                role=request.role,
                iteration=request.execution_iteration,
                request_estimated_tokens=estimated_input,
                usage=response.usage,
                tool_argument_tokens=self._tool_argument_tokens(response.tool_calls),
                write_patch_argument_tokens=self._write_patch_argument_tokens(response.tool_calls),
                has_real_progress=request.budget_progress,
                max_output_tokens=request.max_output_tokens,
            )
        )
        if observation_id is not None:
            self._latest_observation_ids[request.role.value] = observation_id
            self._last_max_output_tokens[request.role.value] = request.max_output_tokens
        return response

    async def record_tool_outcome(
        self,
        *,
        role: str,
        calls=(),
        results: list[ToolExecutionResult],
        has_real_progress: bool,
        compacted_code_mutation: bool = False,
    ) -> None:
        """Complete the current cost fact after controlled tool execution."""

        observation_id = self._latest_observation_ids.get(role)
        if observation_id is None:
            return
        result_tokens = sum(
            self._token_estimator.billable_token_estimate(result.model_dump_json())
            for result in results
        )
        record_outcome = getattr(self._budget_store, "record_tool_outcome_observation", None)
        if not callable(record_outcome):
            return
        await record_outcome(
            observation_id=observation_id,
            tool_result_tokens=result_tokens,
            has_real_progress=has_real_progress,
            max_output_tokens=self._max_output_for_role(role),
            compacted_tool_argument_tokens=(
                sum(self._token_estimator.billable_token_estimate(call.arguments) for call in calls)
                if compacted_code_mutation
                else 0
            ),
        )

    @staticmethod
    def _tool_argument_tokens(calls) -> int:  # type: ignore[no-untyped-def]
        estimator = TokenEstimator()
        return sum(estimator.billable_token_estimate(call.arguments) for call in calls)

    @staticmethod
    def _write_patch_argument_tokens(calls) -> int:  # type: ignore[no-untyped-def]
        estimator = TokenEstimator()
        return sum(
            estimator.billable_token_estimate(call.arguments)
            for call in calls
            if call.name in {"write_file", "apply_patch"}
        )

    def _max_output_for_role(self, role: str) -> int:
        # The response's maximum is not retained by ToolExecutionResult. The latest
        # reservation already included it; using the configured per-call cap keeps the
        # follow-up prediction conservative for Developer and Repair.
        return max(64, self._last_max_output_tokens.get(role, 1_400))

    def _estimate_request_tokens(self, request: AgentRequest) -> int:
        return self._token_estimator.estimate_agent_request(request)
