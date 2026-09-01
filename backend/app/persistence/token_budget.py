from __future__ import annotations

# ruff: noqa: E501
import json
from dataclasses import dataclass
from math import ceil
from typing import Literal
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentRole, LivenessCredit, TokenUsage
from app.models.dag import TaskDAG
from app.models.token_budget import (
    RoleTokenUsage,
    RunTokenBudget,
    RunTokenBudgetStatus,
    StageTokenBudget,
    TaskBudgetStatus,
    TokenBudgetStage,
    WorkPackageTokenBudget,
)
from app.models.workflow import WorkflowExecutionMode
from app.persistence.database import create_postgres_engine, create_session_factory


class TokenBudgetReservationError(RuntimeError):
    """Raised before a provider call when a Run cannot reserve enough tokens."""


class WorkPackageBudgetAllocationError(TokenBudgetReservationError):
    """A recoverable work-package allocation block rather than a provider failure."""

    def __init__(
        self, message: str, *, decision: BudgetDecisionFacts | None = None
    ) -> None:
        super().__init__(message)
        self.decision = decision


class TokenBudgetPlanError(RuntimeError):
    """Raised when a Planner proposal cannot fund its own minimum agent turns."""


@dataclass(frozen=True)
class TokenBudgetReservation:
    reservation_id: UUID
    run_id: UUID
    role: AgentRole
    reserved_input_tokens: int
    reserved_output_tokens: int

    @property
    def reserved_tokens(self) -> int:
        return self.reserved_input_tokens + self.reserved_output_tokens


@dataclass(frozen=True)
class BudgetDecisionFacts:
    """The exact decision observed under a locked budget snapshot.

    This is deliberately small and scalar-only so a rejected reservation can be
    committed in a separate transaction after the reservation transaction rolls
    back.  The dashboard therefore never has to infer a rejection from stale
    counters left by a previous provider turn.
    """

    run_id: UUID
    task_id: str
    role: AgentRole
    estimated_input_tokens: int
    max_output_tokens: int
    required_tokens: int
    package_available_tokens: int
    flex_available_tokens: int
    downstream_available_tokens: int
    borrowed_tokens: int
    decision: Literal["RESERVED", "BORROWED", "DENIED"]
    reason: str | None
    limit_flags: tuple[str, ...] = ()
    recovery_action: str | None = None


class PostgresRunTokenBudgetStore:
    """Atomic Run-level token reservations shared safely by concurrent Workers."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        default_total_budget_tokens: int,
        adaptive_package_budget_enabled: bool = True,
        token_estimate_safety_factor: float = 1.15,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._default_total_budget_tokens = default_total_budget_tokens
        self._adaptive_package_budget_enabled = adaptive_package_budget_enabled
        self._token_estimate_safety_factor = token_estimate_safety_factor
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        default_total_budget_tokens: int,
        adaptive_package_budget_enabled: bool = True,
        token_estimate_safety_factor: float = 1.15,
        echo: bool = False,
    ) -> PostgresRunTokenBudgetStore:
        return cls(
            engine=create_postgres_engine(database_url, echo=echo),
            default_total_budget_tokens=default_total_budget_tokens,
            adaptive_package_budget_enabled=adaptive_package_budget_enabled,
            token_estimate_safety_factor=token_estimate_safety_factor,
            owns_engine=True,
        )

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def initialize(self, run_id: UUID, *, total_budget_tokens: int | None = None) -> None:
        budget = total_budget_tokens or self._default_total_budget_tokens
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO run_token_budgets (run_id, total_budget_tokens) "
                    "VALUES (:run_id, :total) ON CONFLICT (run_id) DO NOTHING"
                ),
                {"run_id": run_id, "total": budget},
            )

    async def initialize_hierarchy(
        self,
        *,
        run_id: UUID,
        dag: TaskDAG,
        developer_max_output_tokens: int = 1_400,
    ) -> None:
        """Allocate bounded work-package pools before the first task is dispatched.

        Reservations are still made per provider call.  These pools prevent the first active
        task from consuming the amounts explicitly reserved for independent downstream work.
        Legacy DAGs have no work-package metadata and retain the previous Run-only behavior.
        """
        nodes = tuple(node for node in dag.tasks if node.budget_allocation is not None)
        if not nodes:
            return
        self.validate_hierarchy_plan(
            dag=dag, developer_max_output_tokens=developer_max_output_tokens
        )
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens FROM run_token_budgets WHERE run_id = :run_id FOR UPDATE"
                    ),
                    {"run_id": run_id},
                )
            ).one_or_none()
            total = (
                self._default_total_budget_tokens if row is None else int(row.total_budget_tokens)
            )
            if row is None:
                await session.execute(
                    text(
                        "INSERT INTO run_token_budgets (run_id, total_budget_tokens) VALUES (:run_id, :total)"
                    ),
                    {"run_id": run_id, "total": total},
                )
            # Planning has already been settled against the launch-scoped PlanningTokenBudget
            # before a Run exists.  Keeping a second planning pool here strands usable Run
            # capacity after the DAG is accepted, precisely when the critical work package may
            # need another bounded turn.  Its Run share therefore enters FLEX immediately.
            planning = 0
            # Review/publication is a terminal, bounded activity.  It keeps only a small floor;
            # all surplus belongs to the execution critical path until it is actually needed.
            review_publication = min(1_000, max(500, total // 30))
            raw = [
                self._initial_package_allocation(node, developer_max_output_tokens)
                for node in nodes
            ]
            developer_total = 0
            repair_total = 0
            for node, developer, repair in raw:
                if node.execution_mode is WorkflowExecutionMode.WORKFLOW:
                    developer = 0
                    repair = 0
                package_total = developer + repair
                developer_total += developer
                repair_total += repair
                await session.execute(
                    text(
                        "INSERT INTO run_task_token_budgets "
                        "(run_id, task_id, complexity, total_budget_tokens, developer_budget_tokens, "
                        "repair_budget_tokens, developer_startup_reserve_tokens) "
                        "VALUES (:run_id, :task_id, :complexity, :total, :developer, :repair, :startup) "
                        "ON CONFLICT (run_id, task_id) DO NOTHING"
                    ),
                    {
                        "run_id": run_id,
                        "task_id": node.task.task_id,
                        "complexity": node.complexity.value
                        if node.complexity is not None
                        else "MEDIUM",
                        "total": package_total,
                        "developer": developer,
                        "repair": repair,
                        # One normal Developer turn remains protected. The second
                        # startup turn is deferred capacity: while a direct dependant
                        # is blocked by this producer it may be loaned safely and is
                        # settled before the dependant can be dispatched.
                        "startup": developer // 2,
                    },
                )
            stage_budgets = {
                TokenBudgetStage.PLANNING: planning,
                TokenBudgetStage.DEVELOPMENT: developer_total,
                TokenBudgetStage.VERIFICATION_REPAIR: repair_total,
                TokenBudgetStage.REVIEW_PUBLICATION: review_publication,
                TokenBudgetStage.FLEX: max(
                    0,
                    total - planning - developer_total - repair_total - review_publication,
                ),
            }
            for stage, amount in stage_budgets.items():
                await session.execute(
                    text(
                        "INSERT INTO run_stage_token_budgets (run_id, stage, total_budget_tokens) "
                        "VALUES (:run_id, :stage, :total) ON CONFLICT (run_id, stage) DO NOTHING"
                    ),
                    {"run_id": run_id, "stage": stage.value, "total": amount},
                )

    def validate_hierarchy_plan(
        self, *, dag: TaskDAG, developer_max_output_tokens: int = 1_400
    ) -> None:
        nodes = tuple(node for node in dag.tasks if node.budget_allocation is not None)
        if not nodes:
            return
        total = self._default_total_budget_tokens
        # The planner has its own launch budget.  Run budget validation must match the
        # post-planning hierarchy created above, otherwise it rejects plans based on capacity
        # that will in fact be available to FLEX.
        planning = 0
        review_publication = min(1_000, max(500, total // 30))
        required_packages = sum(
            developer + repair
            for _, developer, repair in (
                self._initial_package_allocation(node, developer_max_output_tokens)
                for node in nodes
            )
        )
        available_for_packages = max(0, total - planning - review_publication)
        if required_packages > available_for_packages:
            raise TokenBudgetPlanError(
                "工作包预算计划不可执行：最低开发轮次需要 "
                f"{required_packages} Token，但在规划与审查预留后仅有 "
                f"{available_for_packages} Token。请拆分任务或提高本次运行预算。"
            )

    async def reserve(
        self,
        *,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        estimated_input_tokens: int,
        max_output_tokens: int,
        has_progress: bool = False,
        liveness_credit: LivenessCredit = LivenessCredit.NORMAL,
    ) -> TokenBudgetReservation:
        if estimated_input_tokens < 0 or max_output_tokens < 1:
            raise ValueError("token reservation values must be non-negative and non-empty")
        reservation_id = uuid4()
        try:
            async with self._session_factory.begin() as session:
                estimated_input_tokens = await self._calibrated_input_estimate(
                    session=session,
                    run_id=run_id,
                    task_id=task_id,
                    role=role,
                    estimated_input_tokens=estimated_input_tokens,
                )
                required = estimated_input_tokens + max_output_tokens
                await session.execute(
                    text(
                        "INSERT INTO run_token_budgets (run_id, total_budget_tokens) "
                        "VALUES (:run_id, :total) ON CONFLICT (run_id) DO NOTHING"
                    ),
                    {"run_id": run_id, "total": self._default_total_budget_tokens},
                )
                row = (
                    await session.execute(
                        text(
                            "SELECT total_budget_tokens, used_total_tokens, reserved_tokens "
                            "FROM run_token_budgets WHERE run_id = :run_id FOR UPDATE"
                        ),
                        {"run_id": run_id},
                    )
                ).one()
                total = int(row.total_budget_tokens)
                projected = int(row.used_total_tokens) + int(row.reserved_tokens) + required
                status = self._status_for(projected, total)
                if projected > total:
                    await self._update_status(session, run_id, RunTokenBudgetStatus.EXHAUSTED)
                    raise TokenBudgetReservationError("本次运行模型预算已用尽，未向模型服务发起请求。")
                if status is RunTokenBudgetStatus.CRITICAL and role not in {
                    AgentRole.DEVELOPER,
                    AgentRole.REPAIR,
                }:
                    await self._update_status(session, run_id, status)
                    raise TokenBudgetReservationError(
                        "本次运行模型预算已进入临界区，仅允许开发或修复调用。"
                    )
                await self._reserve_hierarchy(
                    session=session,
                    run_id=run_id,
                    task_id=task_id,
                    role=role,
                    required=required,
                    estimated_input_tokens=estimated_input_tokens,
                    max_output_tokens=max_output_tokens,
                    has_progress=has_progress,
                    liveness_credit=liveness_credit,
                )
                await session.execute(
                    text(
                        "UPDATE run_token_budgets SET reserved_tokens = reserved_tokens + :required, "
                        "status = :status, updated_at = now() WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id, "required": required, "status": status.value},
                )
                await session.execute(
                    text(
                        "INSERT INTO run_token_reservations "
                        "(reservation_id, run_id, task_id, role, reserved_input_tokens, "
                        "reserved_output_tokens) "
                        "VALUES (:reservation_id, :run_id, :task_id, :role, :input, :output)"
                    ),
                    {
                        "reservation_id": reservation_id,
                        "run_id": run_id,
                        "task_id": task_id,
                        "role": role.value,
                        "input": estimated_input_tokens,
                        "output": max_output_tokens,
                    },
                )
        except WorkPackageBudgetAllocationError as exc:
            # The reservation transaction must roll back so no partial borrowing,
            # stage allocation, or reservation survives. Persist the *observed*
            # refusal afterwards in its own transaction.
            if exc.decision is not None:
                await self._persist_budget_decision_independently(exc.decision)
            raise
        return TokenBudgetReservation(
            reservation_id=reservation_id,
            run_id=run_id,
            role=role,
            reserved_input_tokens=estimated_input_tokens,
            reserved_output_tokens=max_output_tokens,
        )

    async def cancel(self, reservation: TokenBudgetReservation) -> None:
        await self._finish_reservation(reservation, usage=None)

    async def settle(self, reservation: TokenBudgetReservation, usage: TokenUsage) -> None:
        await self._finish_reservation(reservation, usage=usage)

    async def record_usage(self, *, run_id: UUID, role: AgentRole, usage: TokenUsage) -> None:
        """Record planning usage that happened before the persisted Run was created."""

        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE run_token_budgets SET used_prompt_tokens = "
                    "used_prompt_tokens + :prompt, "
                    "used_completion_tokens = used_completion_tokens + :completion, "
                    "used_total_tokens = used_total_tokens + :total, "
                    "status = CASE "
                    "WHEN used_total_tokens + :total >= total_budget_tokens THEN 'EXHAUSTED' "
                    "WHEN (used_total_tokens + :total) * 100 >= total_budget_tokens * 90 "
                    "THEN 'CRITICAL' "
                    "WHEN (used_total_tokens + :total) * 100 >= total_budget_tokens * 70 "
                    "THEN 'WARNING' "
                    "ELSE status END, updated_at = now() "
                    "WHERE run_id = :run_id"
                ),
                {
                    "run_id": run_id,
                    "prompt": usage.prompt_tokens,
                    "completion": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )
            await self._record_stage_usage(
                session, run_id=run_id, role=role, total=usage.total_tokens
            )
            await session.execute(
                text(
                    "INSERT INTO run_token_role_usage "
                    "(run_id, role, prompt_tokens, completion_tokens, total_tokens, call_count) "
                    "VALUES (:run_id, :role, :prompt, :completion, :total, 1) "
                    "ON CONFLICT (run_id, role) DO UPDATE SET "
                    "prompt_tokens = run_token_role_usage.prompt_tokens + EXCLUDED.prompt_tokens, "
                    "completion_tokens = run_token_role_usage.completion_tokens + "
                    "EXCLUDED.completion_tokens, "
                    "total_tokens = run_token_role_usage.total_tokens + EXCLUDED.total_tokens, "
                    "call_count = run_token_role_usage.call_count + 1, updated_at = now()"
                ),
                {
                    "run_id": run_id,
                    "role": role.value,
                    "prompt": usage.prompt_tokens,
                    "completion": usage.completion_tokens,
                    "total": usage.total_tokens,
                },
            )

    async def record_model_turn_observation(
        self,
        *,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        iteration: int,
        request_estimated_tokens: int,
        usage: TokenUsage,
        tool_argument_tokens: int,
        write_patch_argument_tokens: int,
        has_real_progress: bool,
        max_output_tokens: int,
    ) -> int | None:
        """Persist bounded cost facts after provider settlement.

        The observation contains only token counts and a progress bit. Raw source,
        tool arguments and results remain outside PostgreSQL diagnostics.
        """

        if role not in {AgentRole.DEVELOPER, AgentRole.REPAIR}:
            return None
        prefix = "developer" if role is AgentRole.DEVELOPER else "repair"
        async with self._session_factory.begin() as session:
            previous = (
                await session.execute(
                    text(
                        "SELECT actual_prompt_tokens FROM run_task_cost_observations "
                        "WHERE run_id = :run_id AND task_id = :task_id AND role = :role "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"run_id": run_id, "task_id": task_id, "role": role.value},
                )
            ).one_or_none()
            prior_prompt = 0 if previous is None else int(previous.actual_prompt_tokens)
            growth = max(0, usage.prompt_tokens - prior_prompt)
            observation = await session.execute(
                text(
                    "INSERT INTO run_task_cost_observations "
                    "(run_id, task_id, role, iteration, request_estimated_tokens, actual_prompt_tokens, "
                    "actual_completion_tokens, tool_argument_tokens, write_patch_argument_tokens, "
                    "context_growth_tokens, has_real_progress) "
                    "VALUES (:run_id, :task_id, :role, :iteration, :estimated, :prompt, :completion, "
                    ":arguments, :write_patch, :growth, :progress) RETURNING id"
                ),
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "role": role.value,
                    "iteration": iteration,
                    "estimated": request_estimated_tokens,
                    "prompt": usage.prompt_tokens,
                    "completion": usage.completion_tokens,
                    "arguments": tool_argument_tokens,
                    "write_patch": write_patch_argument_tokens,
                    "growth": growth,
                    "progress": has_real_progress,
                },
            )
            observation_id = int(observation.scalar_one())
            predicted = self._next_input_prediction(
                current_request_estimate=request_estimated_tokens,
                observed_prompt_tokens=usage.prompt_tokens,
                context_growth_tokens=growth,
                tool_argument_tokens=tool_argument_tokens,
                tool_result_tokens=0,
                write_patch_argument_tokens=write_patch_argument_tokens,
            )
            await self._update_cost_prediction(
                session=session,
                run_id=run_id,
                task_id=task_id,
                prefix=prefix,
                predicted_input_tokens=predicted,
                max_output_tokens=max_output_tokens,
                reason="当前请求、实际 Prompt、上下文增长和工具参数的最大预测值。",
            )
            return observation_id

    async def record_tool_outcome_observation(
        self,
        *,
        observation_id: int,
        tool_result_tokens: int,
        has_real_progress: bool,
        max_output_tokens: int,
        compacted_tool_argument_tokens: int = 0,
    ) -> None:
        """Complete the latest model-turn observation after controlled tools return."""

        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT run_id, task_id, role, request_estimated_tokens, actual_prompt_tokens, "
                        "tool_argument_tokens, write_patch_argument_tokens, context_growth_tokens, "
                        "compacted_tool_argument_tokens "
                        "FROM run_task_cost_observations WHERE id = :id FOR UPDATE"
                    ),
                    {"id": observation_id},
                )
            ).one_or_none()
            if row is None:
                return
            await session.execute(
                text(
                    "UPDATE run_task_cost_observations SET tool_result_tokens = :results, "
                    "compacted_tool_argument_tokens = :compacted_arguments, "
                    "has_real_progress = has_real_progress OR :progress WHERE id = :id"
                ),
                {
                    "id": observation_id,
                    "results": tool_result_tokens,
                    "compacted_arguments": compacted_tool_argument_tokens,
                    "progress": has_real_progress,
                },
            )
            prefix = "developer" if str(row.role) == AgentRole.DEVELOPER.value else "repair"
            # A successful write/apply_patch group is omitted from the following model
            # conversation.  Both the all-tool and write/patch predictors must exclude
            # its payload; subtracting it from only one branch still lets the write floor
            # recreate the same source-sized reservation.
            retained_tool_arguments = max(
                0, int(row.tool_argument_tokens) - compacted_tool_argument_tokens
            )
            retained_write_patch_arguments = max(
                0, int(row.write_patch_argument_tokens) - compacted_tool_argument_tokens
            )
            predicted = self._next_input_prediction(
                current_request_estimate=int(row.request_estimated_tokens),
                observed_prompt_tokens=int(row.actual_prompt_tokens),
                context_growth_tokens=int(row.context_growth_tokens),
                tool_argument_tokens=retained_tool_arguments,
                tool_result_tokens=tool_result_tokens,
                write_patch_argument_tokens=retained_write_patch_arguments,
            )
            await self._update_cost_prediction(
                session=session,
                run_id=row.run_id,
                task_id=str(row.task_id),
                prefix=prefix,
                predicted_input_tokens=predicted,
                max_output_tokens=max_output_tokens,
                reason="当前请求、实际 Prompt、增长率、工具结果与大写入参数的最大预测值。",
            )

    async def reclaim_unused_task_budget(self, *, run_id: UUID, task_id: str) -> None:
        """Return a successfully completed package's unused Agent pools to FLEX."""

        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT developer_budget_tokens, repair_budget_tokens, developer_used_tokens, "
                        "repair_used_tokens, developer_reserved_tokens, repair_reserved_tokens, status "
                        "FROM run_task_token_budgets WHERE run_id = :run_id AND task_id = :task_id FOR UPDATE"
                    ),
                    {"run_id": run_id, "task_id": task_id},
                )
            ).one_or_none()
            if row is None or str(row.status) == TaskBudgetStatus.RECLAIMED.value:
                return
            developer = max(
                0,
                int(row.developer_budget_tokens)
                - int(row.developer_used_tokens)
                - int(row.developer_reserved_tokens),
            )
            repair = max(
                0,
                int(row.repair_budget_tokens)
                - int(row.repair_used_tokens)
                - int(row.repair_reserved_tokens),
            )
            loan_rows = (
                await session.execute(
                    text(
                        "SELECT id, lender_task_id, amount_tokens FROM run_task_budget_loans "
                        "WHERE run_id = :run_id AND borrower_task_id = :task_id AND state = 'ACTIVE' "
                        "ORDER BY id FOR UPDATE"
                    ),
                    {"run_id": run_id, "task_id": task_id},
                )
            ).all()
            # A lender only ever contributed capacity above its startup reserve.  Return
            # the unused portion before releasing the borrower's remaining capacity to
            # FLEX; consumed loan capacity is correctly settled as Run expenditure.
            remaining_for_loans = developer
            returned_to_lenders = 0
            for loan in loan_rows:
                returned = min(remaining_for_loans, int(loan.amount_tokens))
                if returned:
                    await session.execute(
                        text(
                            "UPDATE run_task_token_budgets SET total_budget_tokens = total_budget_tokens + :amount, "
                            "developer_budget_tokens = developer_budget_tokens + :amount "
                            "WHERE run_id = :run_id AND task_id = :task_id"
                        ),
                        {
                            "run_id": run_id,
                            "task_id": str(loan.lender_task_id),
                            "amount": returned,
                        },
                    )
                    remaining_for_loans -= returned
                    returned_to_lenders += returned
                await session.execute(
                    text(
                        "UPDATE run_task_budget_loans SET state = 'SETTLED', settled_at = now() WHERE id = :id"
                    ),
                    {"id": int(loan.id)},
                )
            if returned_to_lenders:
                await session.execute(
                    text(
                        "UPDATE run_task_token_budgets SET total_budget_tokens = total_budget_tokens - :amount, "
                        "developer_budget_tokens = developer_budget_tokens - :amount "
                        "WHERE run_id = :run_id AND task_id = :task_id"
                    ),
                    {"run_id": run_id, "task_id": task_id, "amount": returned_to_lenders},
                )
            developer = remaining_for_loans
            reclaimed = developer + repair
            if reclaimed:
                await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens - :amount "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.DEVELOPMENT.value, "amount": developer},
                )
                await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens - :amount "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.VERIFICATION_REPAIR.value, "amount": repair},
                )
                await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens + :amount "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.FLEX.value, "amount": reclaimed},
                )
            await session.execute(
                text(
                    "UPDATE run_task_token_budgets SET developer_reclaimed_tokens = developer_reclaimed_tokens + :developer, "
                    "repair_reclaimed_tokens = repair_reclaimed_tokens + :repair, status = 'RECLAIMED' "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {"run_id": run_id, "task_id": task_id, "developer": developer, "repair": repair},
            )

    async def snapshot(self, run_id: UUID) -> RunTokenBudget:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_prompt_tokens, used_completion_tokens, "
                        "used_total_tokens, reserved_tokens, status FROM run_token_budgets "
                        "WHERE run_id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).one_or_none()
            role_rows = (
                await session.execute(
                    text(
                        "SELECT role, prompt_tokens, completion_tokens, total_tokens, call_count "
                        "FROM run_token_role_usage WHERE run_id = :run_id ORDER BY role"
                    ),
                    {"run_id": run_id},
                )
            ).all()
            stage_rows = (
                await session.execute(
                    text(
                        "SELECT stage, total_budget_tokens, used_tokens, reserved_tokens FROM run_stage_token_budgets WHERE run_id = :run_id ORDER BY stage"
                    ),
                    {"run_id": run_id},
                )
            ).all()
            package_rows = (
                await session.execute(
                    text(
                        "SELECT task_id, complexity, total_budget_tokens, developer_budget_tokens, repair_budget_tokens, developer_used_tokens, repair_used_tokens, developer_reserved_tokens, repair_reserved_tokens, developer_borrowed_tokens, repair_borrowed_tokens, developer_reclaimed_tokens, repair_reclaimed_tokens, developer_observed_prompt_tokens, repair_observed_prompt_tokens, developer_predicted_next_input_tokens, repair_predicted_next_input_tokens, developer_estimated_executable_turns, repair_estimated_executable_turns, developer_startup_reserve_tokens, complexity_upgrade_count, borrow_count, tool_recovery_credit_used, last_liveness_credit, last_required_tokens, last_available_tokens, last_flex_available_tokens, last_downstream_available_tokens, last_borrowed_tokens, last_budget_decision, last_budget_reason, last_recovery_action, last_cost_prediction_reason, status FROM run_task_token_budgets WHERE run_id = :run_id ORDER BY task_id"
                    ),
                    {"run_id": run_id},
                )
            ).all()
        if row is None:
            return RunTokenBudget(total_budget_tokens=self._default_total_budget_tokens)
        return RunTokenBudget(
            total_budget_tokens=int(row.total_budget_tokens),
            used_prompt_tokens=int(row.used_prompt_tokens),
            used_completion_tokens=int(row.used_completion_tokens),
            used_total_tokens=int(row.used_total_tokens),
            reserved_tokens=int(row.reserved_tokens),
            status=RunTokenBudgetStatus(str(row.status)),
            roles=tuple(
                RoleTokenUsage(
                    role=AgentRole(str(item.role)),
                    prompt_tokens=int(item.prompt_tokens),
                    completion_tokens=int(item.completion_tokens),
                    total_tokens=int(item.total_tokens),
                    call_count=int(item.call_count),
                )
                for item in role_rows
            ),
            stages=tuple(
                StageTokenBudget(
                    stage=TokenBudgetStage(str(item.stage)),
                    total_budget_tokens=int(item.total_budget_tokens),
                    used_tokens=int(item.used_tokens),
                    reserved_tokens=int(item.reserved_tokens),
                )
                for item in stage_rows
            ),
            work_packages=tuple(
                WorkPackageTokenBudget(
                    task_id=str(item.task_id),
                    complexity=str(item.complexity),
                    total_budget_tokens=int(item.total_budget_tokens),
                    developer_budget_tokens=int(item.developer_budget_tokens),
                    repair_budget_tokens=int(item.repair_budget_tokens),
                    developer_used_tokens=int(item.developer_used_tokens),
                    repair_used_tokens=int(item.repair_used_tokens),
                    developer_reserved_tokens=int(item.developer_reserved_tokens),
                    repair_reserved_tokens=int(item.repair_reserved_tokens),
                    developer_borrowed_tokens=int(item.developer_borrowed_tokens),
                    repair_borrowed_tokens=int(item.repair_borrowed_tokens),
                    developer_reclaimed_tokens=int(item.developer_reclaimed_tokens),
                    repair_reclaimed_tokens=int(item.repair_reclaimed_tokens),
                    developer_observed_prompt_tokens=int(item.developer_observed_prompt_tokens),
                    repair_observed_prompt_tokens=int(item.repair_observed_prompt_tokens),
                    developer_predicted_next_input_tokens=int(
                        item.developer_predicted_next_input_tokens
                    ),
                    repair_predicted_next_input_tokens=int(item.repair_predicted_next_input_tokens),
                    developer_estimated_executable_turns=int(
                        item.developer_estimated_executable_turns
                    ),
                    repair_estimated_executable_turns=int(item.repair_estimated_executable_turns),
                    developer_startup_reserve_tokens=int(item.developer_startup_reserve_tokens),
                    complexity_upgrade_count=int(item.complexity_upgrade_count),
                    borrow_count=int(item.borrow_count),
                    tool_recovery_credit_used=bool(item.tool_recovery_credit_used),
                    last_liveness_credit=LivenessCredit(str(item.last_liveness_credit)),
                    last_required_tokens=int(item.last_required_tokens),
                    last_available_tokens=int(item.last_available_tokens),
                    last_flex_available_tokens=int(item.last_flex_available_tokens),
                    last_downstream_available_tokens=int(item.last_downstream_available_tokens),
                    last_borrowed_tokens=int(item.last_borrowed_tokens),
                    last_budget_decision=(
                        str(item.last_budget_decision)
                        if item.last_budget_decision is not None
                        else None
                    ),
                    last_budget_reason=(
                        str(item.last_budget_reason)
                        if item.last_budget_reason is not None
                        else None
                    ),
                    last_recovery_action=(
                        str(item.last_recovery_action)
                        if item.last_recovery_action is not None
                        else None
                    ),
                    last_cost_prediction_reason=(
                        str(item.last_cost_prediction_reason)
                        if item.last_cost_prediction_reason is not None
                        else None
                    ),
                    status=TaskBudgetStatus(str(item.status)),
                )
                for item in package_rows
            ),
        )

    async def _finish_reservation(
        self,
        reservation: TokenBudgetReservation,
        *,
        usage: TokenUsage | None,
    ) -> None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT task_id, role, reserved_input_tokens, reserved_output_tokens "
                        "FROM run_token_reservations "
                        "WHERE reservation_id = :reservation_id FOR UPDATE"
                    ),
                    {"reservation_id": reservation.reservation_id},
                )
            ).one_or_none()
            if row is None:
                return
            reserved = int(row.reserved_input_tokens) + int(row.reserved_output_tokens)
            budget = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_total_tokens FROM run_token_budgets "
                        "WHERE run_id = :run_id FOR UPDATE"
                    ),
                    {"run_id": reservation.run_id},
                )
            ).one()
            prompt, completion = self._actual_usage(usage, reservation)
            total = prompt + completion
            status = self._status_for(
                int(budget.used_total_tokens) + total,
                int(budget.total_budget_tokens),
            )
            await session.execute(
                text(
                    "UPDATE run_token_budgets SET reserved_tokens = "
                    "GREATEST(0, reserved_tokens - :reserved), "
                    "used_prompt_tokens = used_prompt_tokens + :prompt, "
                    "used_completion_tokens = used_completion_tokens + :completion, "
                    "used_total_tokens = used_total_tokens + :total, status = :status, "
                    "updated_at = now() "
                    "WHERE run_id = :run_id"
                ),
                {
                    "run_id": reservation.run_id,
                    "reserved": reserved,
                    "prompt": prompt,
                    "completion": completion,
                    "total": total,
                    "status": status.value,
                },
            )
            await self._settle_hierarchy(
                session=session,
                run_id=reservation.run_id,
                task_id=str(row.task_id),
                role=AgentRole(str(row.role)),
                reserved=reserved,
                used=total,
                prompt_tokens=prompt,
            )
            if usage is not None:
                await session.execute(
                    text(
                        "INSERT INTO run_token_role_usage "
                        "(run_id, role, prompt_tokens, completion_tokens, total_tokens, "
                        "call_count) "
                        "VALUES (:run_id, :role, :prompt, :completion, :total, 1) "
                        "ON CONFLICT (run_id, role) DO UPDATE SET "
                        "prompt_tokens = run_token_role_usage.prompt_tokens + "
                        "EXCLUDED.prompt_tokens, "
                        "completion_tokens = run_token_role_usage.completion_tokens + "
                        "EXCLUDED.completion_tokens, "
                        "total_tokens = run_token_role_usage.total_tokens + EXCLUDED.total_tokens, "
                        "call_count = run_token_role_usage.call_count + 1, updated_at = now()"
                    ),
                    {
                        "run_id": reservation.run_id,
                        "role": reservation.role.value,
                        "prompt": prompt,
                        "completion": completion,
                        "total": total,
                    },
                )
            await session.execute(
                text("DELETE FROM run_token_reservations WHERE reservation_id = :reservation_id"),
                {"reservation_id": reservation.reservation_id},
            )

    @staticmethod
    def _actual_usage(
        usage: TokenUsage | None, reservation: TokenBudgetReservation
    ) -> tuple[int, int]:
        if usage is None:
            return 0, 0
        if usage.total_tokens == 0 and usage.prompt_tokens == 0 and usage.completion_tokens == 0:
            # An absent provider usage payload must not make requests unmetered.
            return reservation.reserved_input_tokens, reservation.reserved_output_tokens
        return usage.prompt_tokens, usage.completion_tokens

    @staticmethod
    def _status_for(consumed_or_reserved: int, total: int) -> RunTokenBudgetStatus:
        if consumed_or_reserved >= total:
            return RunTokenBudgetStatus.EXHAUSTED
        if consumed_or_reserved * 100 >= total * 90:
            return RunTokenBudgetStatus.CRITICAL
        if consumed_or_reserved * 100 >= total * 70:
            return RunTokenBudgetStatus.WARNING
        return RunTokenBudgetStatus.NORMAL

    @staticmethod
    async def _update_status(
        session: AsyncSession, run_id: UUID, status: RunTokenBudgetStatus
    ) -> None:
        await session.execute(
            text(
                "UPDATE run_token_budgets SET status = :status, updated_at = now() "
                "WHERE run_id = :run_id"
            ),
            {"run_id": run_id, "status": status.value},
        )

    @staticmethod
    def _bounded_package_budget(node) -> int:
        recommended = node.budget_allocation.recommended_token_budget
        complexity = node.complexity.value if node.complexity is not None else "MEDIUM"
        lower, upper = {"LOW": (2_000, 4_000), "MEDIUM": (4_000, 8_000), "HIGH": (8_000, 12_000)}[
            complexity
        ]
        return max(lower, min(upper, recommended))

    def _initial_package_allocation(
        self, node, developer_max_output_tokens: int
    ) -> tuple[object, int, int]:
        """Fund a bounded startup, then let evidence-backed FLEX pay for further work.

        Preallocating every theoretical Developer turn makes a multi-package Run impossible
        before any package has demonstrated progress.  Each package instead receives two normal
        turns (an inspection/tool turn and a follow-up turn) plus a small independent repair
        pool. Further work remains subject to the existing progress, threshold, ceiling and
        atomic FLEX borrowing controls.
        """

        if node.execution_mode is WorkflowExecutionMode.WORKFLOW:
            return node, 0, 0
        if not getattr(self, "_adaptive_package_budget_enabled", True):
            bounded = self._bounded_package_budget(node)
            return node, int(bounded * 0.7), bounded - int(bounded * 0.7)
        # The estimate mirrors the provider-neutral billing heuristic rather than treating
        # UTF-8 bytes as tokens. The floor accounts for role prompts and tool definitions.
        estimator = TokenEstimator()
        predicted_input = max(
            1_200,
            700
            + estimator.billable_token_estimate(node.task.objective)
            + 150 * len(node.task.writable_files)
            + 120 * len(node.task.acceptance_criteria)
            + 120 * len(node.task.verification_commands),
        )
        startup_turns = 2
        developer = startup_turns * (predicted_input + developer_max_output_tokens)
        # Repair has its own starter amount so a first verification failure can be diagnosed,
        # while long repair loops must make progress and borrow from FLEX.
        repair = max(800, min(1_500, predicted_input))
        return node, developer, repair

    def _next_input_prediction(
        self,
        *,
        current_request_estimate: int,
        observed_prompt_tokens: int,
        context_growth_tokens: int,
        tool_argument_tokens: int,
        tool_result_tokens: int,
        write_patch_argument_tokens: int,
    ) -> int:
        """Reserve the largest credible next-turn input, never an optimistic average."""

        observed_floor = ceil(observed_prompt_tokens * self._token_estimate_safety_factor)
        growth_floor = observed_prompt_tokens + ceil(
            max(context_growth_tokens, tool_argument_tokens + tool_result_tokens)
            * self._token_estimate_safety_factor
        )
        write_patch_floor = observed_prompt_tokens + ceil(
            (write_patch_argument_tokens + tool_result_tokens)
            * self._token_estimate_safety_factor
        )
        return max(current_request_estimate, observed_floor, growth_floor, write_patch_floor)

    async def _update_cost_prediction(
        self,
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        prefix: str,
        predicted_input_tokens: int,
        max_output_tokens: int,
        reason: str,
    ) -> None:
        row = (
            await session.execute(
                text(
                    f"SELECT {prefix}_budget_tokens, {prefix}_used_tokens, "
                    f"{prefix}_reserved_tokens FROM run_task_token_budgets "
                    "WHERE run_id = :run_id AND task_id = :task_id FOR UPDATE"
                ),
                {"run_id": run_id, "task_id": task_id},
            )
        ).one_or_none()
        if row is None:
            return
        remaining = max(
            0,
            int(getattr(row, f"{prefix}_budget_tokens"))
            - int(getattr(row, f"{prefix}_used_tokens"))
            - int(getattr(row, f"{prefix}_reserved_tokens")),
        )
        next_turn_cost = max(1, predicted_input_tokens + max_output_tokens)
        await session.execute(
            text(
                f"UPDATE run_task_token_budgets SET {prefix}_predicted_next_input_tokens = :predicted, "
                f"{prefix}_estimated_executable_turns = :turns, last_cost_prediction_reason = :reason "
                "WHERE run_id = :run_id AND task_id = :task_id"
            ),
            {
                "run_id": run_id,
                "task_id": task_id,
                "predicted": predicted_input_tokens,
                "turns": remaining // next_turn_cost,
                "reason": reason,
            },
        )

    async def _calibrated_input_estimate(
        self,
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        estimated_input_tokens: int,
    ) -> int:
        """Use real provider prompt usage as a durable floor for later Agent turns.

        The local request estimator captures the current messages, tools and tool results.
        After a first provider response, the observed prompt size is also persisted per
        work package. The next reservation uses the larger value with a safety margin so a
        tool-heavy conversation cannot surprise the package budget on its next turn.
        """

        if role not in {AgentRole.DEVELOPER, AgentRole.REPAIR}:
            return estimated_input_tokens
        prefix = "developer" if role is AgentRole.DEVELOPER else "repair"
        row = (
            await session.execute(
                text(
                    f"SELECT {prefix}_observed_prompt_tokens "
                    "FROM run_task_token_budgets "
                    "WHERE run_id = :run_id AND task_id = :task_id FOR UPDATE"
                ),
                {"run_id": run_id, "task_id": task_id},
            )
        ).one_or_none()
        observed = 0 if row is None else int(getattr(row, f"{prefix}_observed_prompt_tokens"))
        observation = (
            await session.execute(
                text(
                    "SELECT actual_prompt_tokens, tool_argument_tokens, tool_result_tokens, "
                    "write_patch_argument_tokens, context_growth_tokens, compacted_tool_argument_tokens "
                    "FROM run_task_cost_observations "
                    "WHERE run_id = :run_id AND task_id = :task_id AND role = :role "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"run_id": run_id, "task_id": task_id, "role": role.value},
            )
        ).one_or_none()
        if observation is None:
            return max(
                estimated_input_tokens,
                ceil(observed * self._token_estimate_safety_factor),
            )
        compacted = int(observation.compacted_tool_argument_tokens)
        return self._next_input_prediction(
            current_request_estimate=estimated_input_tokens,
            observed_prompt_tokens=max(observed, int(observation.actual_prompt_tokens)),
            context_growth_tokens=int(observation.context_growth_tokens),
            tool_argument_tokens=max(
                0, int(observation.tool_argument_tokens) - compacted
            ),
            tool_result_tokens=int(observation.tool_result_tokens),
            write_patch_argument_tokens=max(
                0, int(observation.write_patch_argument_tokens) - compacted
            ),
        )

    async def _reserve_hierarchy(
        self,
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        required: int,
        estimated_input_tokens: int,
        max_output_tokens: int,
        has_progress: bool,
        liveness_credit: LivenessCredit = LivenessCredit.NORMAL,
    ) -> None:
        stage = self._stage_for(role)
        stage_row = (
            await session.execute(
                text(
                    "SELECT total_budget_tokens, used_tokens, reserved_tokens FROM run_stage_token_budgets "
                    "WHERE run_id = :run_id AND stage = :stage FOR UPDATE"
                ),
                {"run_id": run_id, "stage": stage.value},
            )
        ).one_or_none()
        if role not in {AgentRole.DEVELOPER, AgentRole.REPAIR}:
            if stage_row is not None and int(stage_row.used_tokens) + int(
                stage_row.reserved_tokens
            ) + required > int(stage_row.total_budget_tokens):
                raise TokenBudgetReservationError(
                    f"{stage.value} 阶段 Token 子预算已用尽，未向模型服务发起请求。"
                )
            if stage_row is not None:
                await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET reserved_tokens = reserved_tokens + :required "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": stage.value, "required": required},
                )
            return

        task_row = (
            await session.execute(
                text(
                    "SELECT complexity, developer_budget_tokens, repair_budget_tokens, "
                    "developer_used_tokens, repair_used_tokens, developer_reserved_tokens, "
                    "repair_reserved_tokens, developer_startup_reserve_tokens, "
                    "complexity_upgrade_count, borrow_count, tool_recovery_credit_used FROM run_task_token_budgets "
                    "WHERE run_id = :run_id AND task_id = :task_id FOR UPDATE"
                ),
                {"run_id": run_id, "task_id": task_id},
            )
        ).one_or_none()
        if task_row is None:
            if stage_row is not None and int(stage_row.used_tokens) + int(
                stage_row.reserved_tokens
            ) + required > int(stage_row.total_budget_tokens):
                raise TokenBudgetReservationError(
                    f"{stage.value} 阶段 Token 子预算已用尽，未向模型服务发起请求。"
                )
        else:
            prefix = "developer" if role is AgentRole.DEVELOPER else "repair"
            available = (
                int(getattr(task_row, f"{prefix}_budget_tokens"))
                - int(getattr(task_row, f"{prefix}_used_tokens"))
                - int(getattr(task_row, f"{prefix}_reserved_tokens"))
            )
            stage_available = (
                int(stage_row.total_budget_tokens)
                - int(stage_row.used_tokens)
                - int(stage_row.reserved_tokens)
                if stage_row is not None
                else required
            )
            flex_row = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_tokens, reserved_tokens FROM run_stage_token_budgets "
                        "WHERE run_id = :run_id AND stage = :stage FOR UPDATE"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.FLEX.value},
                )
            ).one_or_none()
            flex_available = (
                int(flex_row.total_budget_tokens)
                - int(flex_row.used_tokens)
                - int(flex_row.reserved_tokens)
                if flex_row is not None
                else 0
            )
            needed = max(required - available, required - stage_available, 0)
            downstream_available = 0
            if needed:
                current_budget = int(getattr(task_row, f"{prefix}_budget_tokens"))
                complexity = str(task_row.complexity)
                maximum_budget = self._maximum_package_budget(
                    complexity=str(task_row.complexity), current_budget=current_budget
                )
                upgraded = False
                if (
                    current_budget + needed > maximum_budget
                    and complexity == "MEDIUM"
                    and int(task_row.complexity_upgrade_count) == 0
                    and has_progress
                ):
                    # A MEDIUM package which keeps producing verified progress may
                    # receive one controlled ceiling upgrade.  This changes only the
                    # ceiling; it never creates tokens by itself.
                    complexity = "HIGH"
                    maximum_budget = self._maximum_package_budget(
                        complexity=complexity, current_budget=current_budget
                    )
                    upgraded = True

                downstream_rows = await self._dependency_lender_rows(
                    session=session, run_id=run_id, borrower_task_id=task_id, role=role
                )
                downstream_available = sum(
                    max(
                        0,
                        int(item.developer_budget_tokens)
                        - int(item.developer_used_tokens)
                        - int(item.developer_reserved_tokens)
                        - int(item.developer_startup_reserve_tokens),
                    )
                    for item in downstream_rows
                )
                # A producer on the active dependency path may exceed the normal HIGH ceiling
                # once, but only by the amount required for the next turn and never above the
                # explicit critical-path ceiling.  The existing three-borrow limit, real-progress
                # gate and Run total reservation remain hard guards.  This avoids terminating a
                # verified core implementation while its blocked consumers still reserve capacity.
                critical_path_expanded = False
                if (
                    role is AgentRole.DEVELOPER
                    and has_progress
                    and downstream_rows
                    and current_budget + needed > maximum_budget
                ):
                    expanded_ceiling = min(24_000, current_budget + needed)
                    if expanded_ceiling > maximum_budget:
                        maximum_budget = expanded_ceiling
                        critical_path_expanded = True
                stage_shortfall = max(0, required - stage_available)
                flex_borrow = min(needed, flex_available)
                lender_borrow = max(0, needed - flex_borrow)
                flags: list[str] = []
                # Work-package and stage pools are scheduling credits, not a second
                # hard budget. The Run reservation above is the single financial
                # authority.  A producing task with evidence-backed progress must not
                # stop merely because capacity is parked in a legacy stage or in a
                # dependant that cannot run until this task succeeds.
                reason = None
                credit_permits_unverified_borrow = liveness_credit in {
                    LivenessCredit.INITIAL_STARTUP,
                    LivenessCredit.TOOL_RECOVERY,
                    LivenessCredit.CHECKPOINT_RESUME,
                }
                if not has_progress and not credit_permits_unverified_borrow:
                    flags.append("NO_VERIFIED_PROGRESS")
                    reason = "本轮前没有可验证的代码、工具或验证进展。"
                if (
                    liveness_credit is LivenessCredit.TOOL_RECOVERY
                    and bool(getattr(task_row, "tool_recovery_credit_used", False))
                ):
                    flags.append("TOOL_RECOVERY_CREDIT_CONSUMED")
                    reason = "该工作包已使用过一次工具纠错活性信用。"
                if stage_shortfall > flex_available:
                    flags.append("FLEX_STAGE_CAPACITY")
                if lender_borrow > downstream_available:
                    flags.append("DEPENDENT_DOWNSTREAM_RESERVE")
                if current_budget + needed > maximum_budget:
                    flags.append("PACKAGE_CEILING")
                    if reason is None:
                        reason = f"借款后将超过该工作包的 {maximum_budget} Token 上限。"
                if int(task_row.borrow_count) >= 3:
                    flags.append("BORROW_LIMIT")
                    if reason is None:
                        reason = "已达到该工作包的 3 次 FLEX 借款上限。"
                if reason is not None:
                    recovery_action = (
                        "continue_from_checkpoint"
                        if has_progress
                        else "fix_or_replan_work_package"
                    )
                    facts = BudgetDecisionFacts(
                        run_id=run_id,
                        task_id=task_id,
                        role=role,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                        required_tokens=required,
                        package_available_tokens=available,
                        flex_available_tokens=flex_available,
                        downstream_available_tokens=downstream_available,
                        borrowed_tokens=0,
                        decision="DENIED",
                        reason=reason,
                        limit_flags=tuple(flags),
                        recovery_action=recovery_action,
                    )
                    raise WorkPackageBudgetAllocationError(
                        f"工作包 {task_id} {prefix} 预算剩余 {available} Token；下一轮至少需 "
                        f"{required} Token。FLEX 可借 {flex_available} Token；可延期下游预算 "
                        f"{downstream_available} Token，未自动借款：{reason}",
                        decision=facts,
                    )
                assert flex_row is not None
                # Any shortfall left after FLEX and directly blocked dependants is
                # advanced from the Run-level credit.  The outer Run reservation has
                # already locked and checked `used + reserved + required <= total`,
                # so this cannot overspend the Run or double-spend under concurrency.
                global_credit = max(0, lender_borrow - downstream_available)
                if flex_borrow:
                    await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens - :amount "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.FLEX.value, "amount": flex_borrow},
                    )
                if stage_row is not None and flex_borrow:
                    await session.execute(
                        text(
                            "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens + :amount "
                            "WHERE run_id = :run_id AND stage = :stage"
                        ),
                        {"run_id": run_id, "stage": stage.value, "amount": flex_borrow},
                    )
                # Lender capacity may be represented only at the package level in
                # legacy Runs. Bring the development stage up to the required next
                # reservation as an accounting projection; Run-level reservation is
                # still the sole hard cap.
                stage_credit = max(0, stage_shortfall - flex_borrow)
                if stage_row is not None and stage_credit:
                    await session.execute(
                        text(
                            "UPDATE run_stage_token_budgets SET total_budget_tokens = "
                            "total_budget_tokens + :amount WHERE run_id = :run_id AND stage = :stage"
                        ),
                        {
                            "run_id": run_id,
                            "stage": stage.value,
                            "amount": stage_credit,
                        },
                    )
                remaining_lender_borrow = lender_borrow
                for lender in downstream_rows:
                    lender_available = max(
                        0,
                        int(lender.developer_budget_tokens)
                        - int(lender.developer_used_tokens)
                        - int(lender.developer_reserved_tokens)
                        - int(lender.developer_startup_reserve_tokens),
                    )
                    amount = min(remaining_lender_borrow, lender_available)
                    if amount == 0:
                        continue
                    await session.execute(
                        text(
                            "UPDATE run_task_token_budgets SET total_budget_tokens = total_budget_tokens - :amount, "
                            "developer_budget_tokens = developer_budget_tokens - :amount "
                            "WHERE run_id = :run_id AND task_id = :task_id"
                        ),
                        {"run_id": run_id, "task_id": str(lender.task_id), "amount": amount},
                    )
                    await session.execute(
                        text(
                            "INSERT INTO run_task_budget_loans "
                            "(run_id, borrower_task_id, lender_task_id, role, amount_tokens) "
                            "VALUES (:run_id, :borrower, :lender, :role, :amount)"
                        ),
                        {
                            "run_id": run_id,
                            "borrower": task_id,
                            "lender": str(lender.task_id),
                            "role": role.value,
                            "amount": amount,
                        },
                    )
                    remaining_lender_borrow -= amount
                await session.execute(
                    text(
                        f"UPDATE run_task_token_budgets SET total_budget_tokens = total_budget_tokens + :needed, "
                        f"{prefix}_budget_tokens = {prefix}_budget_tokens + :needed, "
                        f"{prefix}_borrowed_tokens = {prefix}_borrowed_tokens + :needed, "
                        "borrow_count = borrow_count + 1, complexity = :complexity, "
                        "complexity_upgrade_count = complexity_upgrade_count + :upgrade, "
                        "last_liveness_credit = :liveness_credit "
                        "WHERE run_id = :run_id AND task_id = :task_id"
                    ),
                    {
                        "run_id": run_id,
                        "task_id": task_id,
                        "needed": needed,
                        "complexity": complexity,
                        "upgrade": 1 if upgraded else 0,
                        "liveness_credit": liveness_credit.value,
                    },
                )
                await self._persist_budget_decision(
                    session=session,
                    facts=BudgetDecisionFacts(
                        run_id=run_id,
                        task_id=task_id,
                        role=role,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                        required_tokens=required,
                        package_available_tokens=available,
                        flex_available_tokens=flex_available,
                        downstream_available_tokens=downstream_available,
                        borrowed_tokens=needed,
                        decision="BORROWED",
                        reason=self._credit_decision_reason(
                            critical_path_expanded=critical_path_expanded,
                            lender_borrow=lender_borrow,
                            global_credit=global_credit,
                            liveness_credit=liveness_credit,
                            has_progress=has_progress,
                        ),
                        limit_flags=tuple(flags),
                    ),
                )
            else:
                await self._persist_budget_decision(
                    session=session,
                    facts=BudgetDecisionFacts(
                        run_id=run_id,
                        task_id=task_id,
                        role=role,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens,
                        required_tokens=required,
                        package_available_tokens=available,
                        flex_available_tokens=flex_available,
                        downstream_available_tokens=0,
                        borrowed_tokens=0,
                        decision="RESERVED",
                        reason=None,
                    ),
                )
            await session.execute(
                text(
                    "UPDATE run_task_token_budgets SET last_required_tokens = :required, "
                    "last_available_tokens = :available, last_flex_available_tokens = :flex, "
                    "last_downstream_available_tokens = :downstream, last_borrowed_tokens = :borrowed, "
                    "last_budget_decision = :decision, last_budget_reason = :reason, "
                    "last_recovery_action = NULL, last_liveness_credit = :liveness_credit, "
                    "tool_recovery_credit_used = tool_recovery_credit_used OR :tool_recovery_credit "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {
                    "run_id": run_id, "task_id": task_id, "required": required,
                    "available": available, "flex": flex_available,
                    "downstream": downstream_available, "borrowed": needed,
                    "decision": "BORROWED" if needed else "RESERVED",
                    "liveness_credit": liveness_credit.value,
                    "tool_recovery_credit": liveness_credit is LivenessCredit.TOOL_RECOVERY,
                    "reason": (
                        self._credit_decision_reason(
                            critical_path_expanded=critical_path_expanded,
                            lender_borrow=lender_borrow,
                            global_credit=global_credit,
                            liveness_credit=liveness_credit,
                            has_progress=has_progress,
                        )
                        if needed
                        else None
                    ),
                },
            )
            await session.execute(
                text(
                    f"UPDATE run_task_token_budgets SET {prefix}_reserved_tokens = {prefix}_reserved_tokens + :required "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {"run_id": run_id, "task_id": task_id, "required": required},
            )
        if stage_row is not None:
            await session.execute(
                text(
                    "UPDATE run_stage_token_budgets SET reserved_tokens = reserved_tokens + :required "
                    "WHERE run_id = :run_id AND stage = :stage"
                ),
                {"run_id": run_id, "stage": stage.value, "required": required},
            )

    @staticmethod
    def _maximum_package_budget(*, complexity: str, current_budget: int) -> int:
        ceiling = {"LOW": 6_000, "MEDIUM": 12_000, "HIGH": 18_000}.get(complexity, 12_000)
        return max(current_budget, ceiling)

    @staticmethod
    def _credit_decision_reason(
        *,
        critical_path_expanded: bool,
        lender_borrow: int,
        global_credit: int,
        liveness_credit: LivenessCredit,
        has_progress: bool,
    ) -> str:
        parts: list[str] = []
        if not has_progress:
            credit_reason = {
                LivenessCredit.INITIAL_STARTUP: "首次受控开发调用使用 Run 启动活性信用",
                LivenessCredit.TOOL_RECOVERY: "一次可恢复工具错误使用 FLEX 纠错活性信用",
                LivenessCredit.CHECKPOINT_RESUME: "检查点恢复使用受控活性信用",
            }.get(liveness_credit)
            if credit_reason is not None:
                parts.append(credit_reason)
        if critical_path_expanded:
            parts.append("关键路径已扩展工作包软上限")
        if global_credit:
            parts.append(f"已从 Run 总预算信用流动 {global_credit} Token")
        elif lender_borrow:
            parts.append("已使用 FLEX 与被阻塞下游的可延期信用")
        else:
            parts.append("已使用 FLEX 信用")
        return "；".join(parts) + "。"

    @staticmethod
    def _borrow_denial_reason(
        *,
        has_progress: bool,
        borrow_count: int,
        needed: int,
        flex_available: int,
        budget: int,
        maximum_budget: int,
    ) -> str | None:
        if not has_progress:
            return "本轮前没有可验证的代码或工具进展。"
        if borrow_count >= 3:
            return "已达到该工作包的 3 次 FLEX 借款上限。"
        if budget + needed > maximum_budget:
            return f"借款后将超过该复杂度的 {maximum_budget} Token 上限。"
        return None

    async def _persist_budget_decision(
        self,
        *,
        session: AsyncSession,
        facts: BudgetDecisionFacts,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO run_token_budget_decisions "
                "(run_id, task_id, role, estimated_input_tokens, max_output_tokens, required_tokens, "
                "package_available_tokens, flex_available_tokens, downstream_available_tokens, "
                "borrowed_tokens, decision, reason, limit_flags, recovery_action) "
                "VALUES (:run_id, :task_id, :role, :input, :output, :required, :available, :flex, "
                ":downstream, :borrowed, :decision, :reason, CAST(:flags AS jsonb), :recovery)"
            ),
            {
                "run_id": facts.run_id,
                "task_id": facts.task_id,
                "role": facts.role.value,
                "input": facts.estimated_input_tokens,
                "output": facts.max_output_tokens,
                "required": facts.required_tokens,
                "available": facts.package_available_tokens,
                "flex": facts.flex_available_tokens,
                "downstream": facts.downstream_available_tokens,
                "borrowed": facts.borrowed_tokens,
                "decision": facts.decision,
                "reason": facts.reason,
                "flags": json.dumps(facts.limit_flags),
                "recovery": facts.recovery_action,
            },
        )

    async def _persist_budget_decision_independently(self, facts: BudgetDecisionFacts) -> None:
        """Durably record a refusal after the reservation transaction has rolled back."""

        async with self._session_factory.begin() as session:
            await self._persist_budget_decision(session=session, facts=facts)
            await session.execute(
                text(
                    "UPDATE run_task_token_budgets SET last_required_tokens = :required, "
                    "last_available_tokens = :available, last_flex_available_tokens = :flex, "
                    "last_downstream_available_tokens = :downstream, last_borrowed_tokens = :borrowed, "
                    "last_budget_decision = :decision, last_budget_reason = :reason, "
                    "last_recovery_action = :recovery "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {
                    "run_id": facts.run_id,
                    "task_id": facts.task_id,
                    "required": facts.required_tokens,
                    "available": facts.package_available_tokens,
                    "flex": facts.flex_available_tokens,
                    "downstream": facts.downstream_available_tokens,
                    "borrowed": facts.borrowed_tokens,
                    "decision": facts.decision,
                    "reason": facts.reason,
                    "recovery": facts.recovery_action,
                },
            )

    @staticmethod
    async def _dependency_lender_rows(
        *,
        session: AsyncSession,
        run_id: UUID,
        borrower_task_id: str,
        role: AgentRole,
    ) -> tuple[object, ...]:
        """Lock only direct, not-yet-ready dependants of the active producer.

        A direct dependant cannot be ready while this producer is still executing;
        a task without this edge may run independently and is never considered.
        Only Developer capacity above its protected startup pool is loanable.
        """

        if role is not AgentRole.DEVELOPER:
            return ()
        result = await session.execute(
            text(
                "SELECT budget.task_id, budget.developer_budget_tokens, budget.developer_used_tokens, "
                "budget.developer_reserved_tokens, budget.developer_startup_reserve_tokens "
                "FROM run_task_token_budgets AS budget "
                "JOIN tasks AS task ON task.run_id = budget.run_id AND task.task_id = budget.task_id "
                "WHERE budget.run_id = :run_id "
                "AND task.depends_on @> CAST(:dependency AS jsonb) "
                "AND budget.status = 'ACTIVE' "
                "ORDER BY budget.task_id FOR UPDATE"
            ),
            {"run_id": run_id, "dependency": json.dumps([borrower_task_id])},
        )
        rows = getattr(result, "all", None)
        return tuple(rows()) if callable(rows) else ()

    async def _settle_hierarchy(
        self,
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        reserved: int,
        used: int,
        prompt_tokens: int,
    ) -> None:
        stage = self._stage_for(role)
        await session.execute(
            text(
                "UPDATE run_stage_token_budgets SET reserved_tokens = GREATEST(0, reserved_tokens - :reserved), used_tokens = used_tokens + :used WHERE run_id = :run_id AND stage = :stage"
            ),
            {"run_id": run_id, "stage": stage.value, "reserved": reserved, "used": used},
        )
        if role in {AgentRole.DEVELOPER, AgentRole.REPAIR}:
            prefix = "developer" if role is AgentRole.DEVELOPER else "repair"
            await session.execute(
                text(
                    f"UPDATE run_task_token_budgets SET {prefix}_reserved_tokens = GREATEST(0, {prefix}_reserved_tokens - :reserved), "
                    f"{prefix}_used_tokens = {prefix}_used_tokens + :used, "
                    f"{prefix}_observed_prompt_tokens = GREATEST({prefix}_observed_prompt_tokens, :prompt) "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "reserved": reserved,
                    "used": used,
                    "prompt": prompt_tokens,
                },
            )

    async def _record_stage_usage(
        self, session: AsyncSession, *, run_id: UUID, role: AgentRole, total: int
    ) -> None:
        stage = self._stage_for(role)
        await session.execute(
            text(
                "UPDATE run_stage_token_budgets SET used_tokens = used_tokens + :total WHERE run_id = :run_id AND stage = :stage"
            ),
            {"run_id": run_id, "stage": stage.value, "total": total},
        )

    @staticmethod
    def _stage_for(role: AgentRole) -> TokenBudgetStage:
        if role is AgentRole.PLANNER:
            return TokenBudgetStage.PLANNING
        if role is AgentRole.REPAIR:
            return TokenBudgetStage.VERIFICATION_REPAIR
        if role is AgentRole.REVIEWER:
            return TokenBudgetStage.REVIEW_PUBLICATION
        return TokenBudgetStage.DEVELOPMENT
