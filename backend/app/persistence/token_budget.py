from __future__ import annotations

# ruff: noqa: E501
from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.agent import AgentRole, TokenUsage
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


class PostgresRunTokenBudgetStore:
    """Atomic Run-level token reservations shared safely by concurrent Workers."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        default_total_budget_tokens: int,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._default_total_budget_tokens = default_total_budget_tokens
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        default_total_budget_tokens: int,
        echo: bool = False,
    ) -> PostgresRunTokenBudgetStore:
        return cls(
            engine=create_postgres_engine(database_url, echo=echo),
            default_total_budget_tokens=default_total_budget_tokens,
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
            planning = min(4_000, max(0, total // 5))
            # Review and publication must never absorb the unallocated development budget.
            # Keep a small, fixed ceiling and place all remaining capacity in FLEX instead.
            review_publication = min(2_000, max(500, total // 10))
            raw = [
                (node, self._initial_package_allocation(node, developer_max_output_tokens))
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
                        "(run_id, task_id, complexity, total_budget_tokens, developer_budget_tokens, repair_budget_tokens) "
                        "VALUES (:run_id, :task_id, :complexity, :total, :developer, :repair) "
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
        planning = min(4_000, max(0, total // 5))
        review_publication = min(2_000, max(500, total // 10))
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
    ) -> TokenBudgetReservation:
        if estimated_input_tokens < 0 or max_output_tokens < 1:
            raise ValueError("token reservation values must be non-negative and non-empty")
        required = estimated_input_tokens + max_output_tokens
        reservation_id = uuid4()
        async with self._session_factory.begin() as session:
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
                        "SELECT task_id, complexity, total_budget_tokens, developer_budget_tokens, repair_budget_tokens, developer_used_tokens, repair_used_tokens, developer_reserved_tokens, repair_reserved_tokens, developer_borrowed_tokens, repair_borrowed_tokens, developer_reclaimed_tokens, repair_reclaimed_tokens, borrow_count, last_required_tokens, last_available_tokens, last_flex_available_tokens, last_borrowed_tokens, last_budget_decision, status FROM run_task_token_budgets WHERE run_id = :run_id ORDER BY task_id"
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
                    borrow_count=int(item.borrow_count),
                    last_required_tokens=int(item.last_required_tokens),
                    last_available_tokens=int(item.last_available_tokens),
                    last_flex_available_tokens=int(item.last_flex_available_tokens),
                    last_borrowed_tokens=int(item.last_borrowed_tokens),
                    last_budget_decision=(
                        str(item.last_budget_decision)
                        if item.last_budget_decision is not None
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

    @classmethod
    def _initial_package_allocation(
        cls, node, developer_max_output_tokens: int
    ) -> tuple[object, int, int]:
        """Fund enough Developer turns before reserving a separate bounded repair pool.

        A request cannot be viable merely because its nominal Planner recommendation is nonzero:
        the agent must be able to return after at least one tool call.  We use a conservative
        1k input floor for the system prompt/tool schema and then apply the declared complexity
        turn count.  Actual usage continues to be settled from the provider response.
        """

        if node.execution_mode is WorkflowExecutionMode.WORKFLOW:
            return node, 0, 0
        complexity = node.complexity.value if node.complexity is not None else "MEDIUM"
        minimum_turns = {"LOW": 2, "MEDIUM": 3, "HIGH": 5}[complexity]
        # Task shape contributes only to the input prediction; the floor accounts for platform
        # instructions and controlled tool definitions even in an empty repository.
        predicted_input = max(
            1_000,
            650
            + len(node.task.objective.encode("utf-8")) // 3
            + 150 * len(node.task.writable_files)
            + 120 * len(node.task.acceptance_criteria)
            + 120 * len(node.task.verification_commands),
        )
        minimum_developer = minimum_turns * (predicted_input + developer_max_output_tokens)
        recommended = cls._bounded_package_budget(node)
        developer = max(minimum_developer, int(recommended * 0.70))
        # Keep recovery independent, but do not starve it merely because the Planner requested
        # a small initial package.  A repair turn can be shorter than a full implementation turn.
        repair = max(1_000, min(developer // 4, max(1_500, recommended // 2)))
        return node, developer, repair

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
                    "repair_reserved_tokens, borrow_count FROM run_task_token_budgets "
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
            if needed:
                consumed = int(getattr(task_row, f"{prefix}_used_tokens")) + int(
                    getattr(task_row, f"{prefix}_reserved_tokens")
                )
                current_budget = int(getattr(task_row, f"{prefix}_budget_tokens"))
                at_threshold = current_budget > 0 and consumed * 100 >= current_budget * 70
                maximum_budget = self._maximum_package_budget(
                    complexity=str(task_row.complexity), current_budget=current_budget
                )
                reason = self._borrow_denial_reason(
                    has_progress=has_progress,
                    at_borrow_threshold=at_threshold,
                    borrow_count=int(task_row.borrow_count),
                    needed=needed,
                    flex_available=flex_available,
                    budget=current_budget,
                    maximum_budget=maximum_budget,
                )
                if reason is not None:
                    await self._persist_budget_decision(
                        session=session, run_id=run_id, task_id=task_id, role=role,
                        estimated_input_tokens=estimated_input_tokens,
                        max_output_tokens=max_output_tokens, required_tokens=required,
                        package_available_tokens=available, flex_available_tokens=flex_available,
                        borrowed_tokens=0, decision="DENIED", reason=reason,
                    )
                    raise TokenBudgetReservationError(
                        f"工作包 {task_id} {prefix} 预算剩余 {available} Token；下一轮至少需 "
                        f"{required} Token。FLEX 可借 {flex_available} Token，但未自动借款：{reason}"
                    )
                assert flex_row is not None
                await session.execute(
                    text(
                        "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens - :needed "
                        "WHERE run_id = :run_id AND stage = :stage"
                    ),
                    {"run_id": run_id, "stage": TokenBudgetStage.FLEX.value, "needed": needed},
                )
                if stage_row is not None:
                    await session.execute(
                        text(
                            "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens + :needed "
                            "WHERE run_id = :run_id AND stage = :stage"
                        ),
                        {"run_id": run_id, "stage": stage.value, "needed": needed},
                    )
                await session.execute(
                    text(
                        f"UPDATE run_task_token_budgets SET {prefix}_budget_tokens = {prefix}_budget_tokens + :needed, "
                        f"{prefix}_borrowed_tokens = {prefix}_borrowed_tokens + :needed, "
                        "borrow_count = borrow_count + 1 WHERE run_id = :run_id AND task_id = :task_id"
                    ),
                    {"run_id": run_id, "task_id": task_id, "needed": needed},
                )
                await self._persist_budget_decision(
                    session=session, run_id=run_id, task_id=task_id, role=role,
                    estimated_input_tokens=estimated_input_tokens,
                    max_output_tokens=max_output_tokens, required_tokens=required,
                    package_available_tokens=available, flex_available_tokens=flex_available,
                    borrowed_tokens=needed, decision="BORROWED",
                    reason="有效工具或代码进展后使用 FLEX 弹性池续接。",
                )
            else:
                await self._persist_budget_decision(
                    session=session, run_id=run_id, task_id=task_id, role=role,
                    estimated_input_tokens=estimated_input_tokens,
                    max_output_tokens=max_output_tokens, required_tokens=required,
                    package_available_tokens=available, flex_available_tokens=flex_available,
                    borrowed_tokens=0, decision="RESERVED", reason=None,
                )
            await session.execute(
                text(
                    "UPDATE run_task_token_budgets SET last_required_tokens = :required, "
                    "last_available_tokens = :available, last_flex_available_tokens = :flex, "
                    "last_borrowed_tokens = :borrowed, last_budget_decision = :decision "
                    "WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {
                    "run_id": run_id, "task_id": task_id, "required": required,
                    "available": available, "flex": flex_available, "borrowed": needed,
                    "decision": "BORROWED" if needed else "RESERVED",
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
    def _borrow_denial_reason(
        *,
        has_progress: bool,
        at_borrow_threshold: bool,
        borrow_count: int,
        needed: int,
        flex_available: int,
        budget: int,
        maximum_budget: int,
    ) -> str | None:
        if not has_progress:
            return "本轮前没有可验证的代码或工具进展。"
        if not at_borrow_threshold:
            return "当前工作包尚未达到 70% 的预算使用阈值。"
        if borrow_count >= 3:
            return "已达到该工作包的 3 次 FLEX 借款上限。"
        if needed > flex_available:
            return "FLEX 弹性池余额不足。"
        if budget + needed > maximum_budget:
            return f"借款后将超过该复杂度的 {maximum_budget} Token 上限。"
        return None

    @staticmethod
    async def _persist_budget_decision(
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        estimated_input_tokens: int,
        max_output_tokens: int,
        required_tokens: int,
        package_available_tokens: int,
        flex_available_tokens: int,
        borrowed_tokens: int,
        decision: str,
        reason: str | None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO run_token_budget_decisions "
                "(run_id, task_id, role, estimated_input_tokens, max_output_tokens, required_tokens, "
                "package_available_tokens, flex_available_tokens, borrowed_tokens, decision, reason) "
                "VALUES (:run_id, :task_id, :role, :input, :output, :required, :available, :flex, "
                ":borrowed, :decision, :reason)"
            ),
            {
                "run_id": run_id,
                "task_id": task_id,
                "role": role.value,
                "input": estimated_input_tokens,
                "output": max_output_tokens,
                "required": required_tokens,
                "available": package_available_tokens,
                "flex": flex_available_tokens,
                "borrowed": borrowed_tokens,
                "decision": decision,
                "reason": reason,
            },
        )

    async def _settle_hierarchy(
        self,
        *,
        session: AsyncSession,
        run_id: UUID,
        task_id: str,
        role: AgentRole,
        reserved: int,
        used: int,
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
                    f"UPDATE run_task_token_budgets SET {prefix}_reserved_tokens = GREATEST(0, {prefix}_reserved_tokens - :reserved), {prefix}_used_tokens = {prefix}_used_tokens + :used WHERE run_id = :run_id AND task_id = :task_id"
                ),
                {"run_id": run_id, "task_id": task_id, "reserved": reserved, "used": used},
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
