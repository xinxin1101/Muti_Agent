from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.agent import TokenUsage
from app.models.token_budget import PlanningTokenBudget, RunTokenBudgetStatus
from app.persistence.database import create_postgres_engine, create_session_factory


class PlanningTokenBudgetReservationError(RuntimeError):
    """Raised before a Planner call when its launch budget is exhausted."""


@dataclass(frozen=True)
class PlanningTokenBudgetReservation:
    reservation_id: UUID
    launch_id: UUID
    reserved_input_tokens: int
    reserved_output_tokens: int


class PostgresPlanningTokenBudgetStore:
    """Reservation-aware Planner budget that exists before a durable Run is created."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        default_total_budget_tokens: int,
        default_max_attempts: int,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._default_total_budget_tokens = default_total_budget_tokens
        self._default_max_attempts = default_max_attempts
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        default_total_budget_tokens: int,
        default_max_attempts: int,
        echo: bool = False,
    ) -> PostgresPlanningTokenBudgetStore:
        return cls(
            engine=create_postgres_engine(database_url, echo=echo),
            default_total_budget_tokens=default_total_budget_tokens,
            default_max_attempts=default_max_attempts,
            owns_engine=True,
        )

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def initialize(
        self,
        *,
        launch_id: UUID,
        project_id: UUID,
        enable_thinking: bool,
    ) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO planning_token_budgets "
                    "(launch_id, project_id, total_budget_tokens, max_attempts, enable_thinking) "
                    "VALUES (:launch_id, :project_id, :total, :max_attempts, :enable_thinking) "
                    "ON CONFLICT (launch_id) DO NOTHING"
                ),
                {
                    "launch_id": launch_id,
                    "project_id": project_id,
                    "total": self._default_total_budget_tokens,
                    "max_attempts": self._default_max_attempts,
                    "enable_thinking": enable_thinking,
                },
            )

    async def reserve(
        self,
        *,
        launch_id: UUID,
        estimated_input_tokens: int,
        max_output_tokens: int,
    ) -> PlanningTokenBudgetReservation:
        if estimated_input_tokens < 0 or max_output_tokens < 1:
            raise ValueError("planning reservation values must be non-negative and non-empty")
        required = estimated_input_tokens + max_output_tokens
        reservation_id = uuid4()
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_total_tokens, reserved_tokens, "
                        "attempt_count, max_attempts FROM planning_token_budgets "
                        "WHERE launch_id = :launch_id FOR UPDATE"
                    ),
                    {"launch_id": launch_id},
                )
            ).one_or_none()
            if row is None:
                raise PlanningTokenBudgetReservationError("规划预算记录不存在，未发起模型请求。")
            if int(row.attempt_count) >= int(row.max_attempts):
                await self._mark_exhausted(session, launch_id)
                raise PlanningTokenBudgetReservationError(
                    "规划模型已达到本次启动的最大调用次数，"
                    f"attempts={int(row.attempt_count)}/{int(row.max_attempts)}，"
                    "未向模型服务发起请求。"
                )
            projected = int(row.used_total_tokens) + int(row.reserved_tokens) + required
            if projected > int(row.total_budget_tokens):
                await self._mark_exhausted(session, launch_id)
                available = max(
                    0,
                    int(row.total_budget_tokens)
                    - int(row.used_total_tokens)
                    - int(row.reserved_tokens),
                )
                raise PlanningTokenBudgetReservationError(
                    "本次启动的规划模型预算已用尽，"
                    f"required_tokens={required}, available_tokens={available}, "
                    f"budget={int(row.total_budget_tokens)}，"
                    "未向模型服务发起请求。"
                )
            status = self._status_for(projected, int(row.total_budget_tokens))
            await session.execute(
                text(
                    "UPDATE planning_token_budgets SET "
                    "reserved_tokens = reserved_tokens + :required, "
                    "attempt_count = attempt_count + 1, status = :status, updated_at = now() "
                    "WHERE launch_id = :launch_id"
                ),
                {"launch_id": launch_id, "required": required, "status": status.value},
            )
            await session.execute(
                text(
                    "INSERT INTO planning_token_reservations "
                    "(reservation_id, launch_id, reserved_input_tokens, reserved_output_tokens) "
                    "VALUES (:reservation_id, :launch_id, :input, :output)"
                ),
                {
                    "reservation_id": reservation_id,
                    "launch_id": launch_id,
                    "input": estimated_input_tokens,
                    "output": max_output_tokens,
                },
            )
        return PlanningTokenBudgetReservation(
            reservation_id=reservation_id,
            launch_id=launch_id,
            reserved_input_tokens=estimated_input_tokens,
            reserved_output_tokens=max_output_tokens,
        )

    async def settle(self, reservation: PlanningTokenBudgetReservation, usage: TokenUsage) -> None:
        await self._finish(reservation, usage=usage)

    async def cancel(self, reservation: PlanningTokenBudgetReservation) -> None:
        await self._finish(reservation, usage=None)

    async def snapshot(self, launch_id: UUID) -> PlanningTokenBudget:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_prompt_tokens, used_completion_tokens, "
                        "used_total_tokens, reserved_tokens, attempt_count, max_attempts, "
                        "enable_thinking, status FROM planning_token_budgets "
                        "WHERE launch_id = :launch_id"
                    ),
                    {"launch_id": launch_id},
                )
            ).one()
        return PlanningTokenBudget(
            launch_id=launch_id,
            total_budget_tokens=int(row.total_budget_tokens),
            used_prompt_tokens=int(row.used_prompt_tokens),
            used_completion_tokens=int(row.used_completion_tokens),
            used_total_tokens=int(row.used_total_tokens),
            reserved_tokens=int(row.reserved_tokens),
            attempt_count=int(row.attempt_count),
            max_attempts=int(row.max_attempts),
            enable_thinking=bool(row.enable_thinking),
            status=RunTokenBudgetStatus(str(row.status)),
        )

    async def snapshot_for_run(self, run_id: UUID) -> PlanningTokenBudget | None:
        async with self._session_factory() as session:
            value = (
                await session.execute(
                    text("SELECT launch_id FROM planning_token_budgets WHERE run_id = :run_id"),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()
        return None if value is None else await self.snapshot(UUID(str(value)))

    async def link_to_run(self, *, launch_id: UUID, run_id: UUID) -> TokenUsage:
        """Return settled planner usage and retain the launch-to-Run audit link."""

        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT used_prompt_tokens, used_completion_tokens, used_total_tokens "
                        "FROM planning_token_budgets WHERE launch_id = :launch_id FOR UPDATE"
                    ),
                    {"launch_id": launch_id},
                )
            ).one()
            await session.execute(
                text(
                    "UPDATE planning_token_budgets SET run_id = :run_id, updated_at = now() "
                    "WHERE launch_id = :launch_id"
                ),
                {"launch_id": launch_id, "run_id": run_id},
            )
        return TokenUsage(
            prompt_tokens=int(row.used_prompt_tokens),
            completion_tokens=int(row.used_completion_tokens),
            total_tokens=int(row.used_total_tokens),
        )

    async def _finish(
        self,
        reservation: PlanningTokenBudgetReservation,
        *,
        usage: TokenUsage | None,
    ) -> None:
        async with self._session_factory.begin() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT reserved_input_tokens, reserved_output_tokens "
                        "FROM planning_token_reservations "
                        "WHERE reservation_id = :reservation_id FOR UPDATE"
                    ),
                    {"reservation_id": reservation.reservation_id},
                )
            ).one_or_none()
            if row is None:
                return
            budget = (
                await session.execute(
                    text(
                        "SELECT total_budget_tokens, used_total_tokens FROM planning_token_budgets "
                        "WHERE launch_id = :launch_id FOR UPDATE"
                    ),
                    {"launch_id": reservation.launch_id},
                )
            ).one()
            reserved_input = int(row.reserved_input_tokens)
            reserved_output = int(row.reserved_output_tokens)
            prompt, completion = self._actual_usage(usage, reserved_input, reserved_output)
            total = prompt + completion
            status = self._status_for(
                int(budget.used_total_tokens) + total, int(budget.total_budget_tokens)
            )
            await session.execute(
                text(
                    "UPDATE planning_token_budgets SET reserved_tokens = "
                    "GREATEST(0, reserved_tokens - :reserved), "
                    "used_prompt_tokens = used_prompt_tokens + :prompt, "
                    "used_completion_tokens = used_completion_tokens + :completion, "
                    "used_total_tokens = used_total_tokens + :total, status = :status, "
                    "updated_at = now() WHERE launch_id = :launch_id"
                ),
                {
                    "launch_id": reservation.launch_id,
                    "reserved": reserved_input + reserved_output,
                    "prompt": prompt,
                    "completion": completion,
                    "total": total,
                    "status": status.value,
                },
            )
            await session.execute(
                text(
                    "DELETE FROM planning_token_reservations WHERE reservation_id = :reservation_id"
                ),
                {"reservation_id": reservation.reservation_id},
            )

    @staticmethod
    def _actual_usage(
        usage: TokenUsage | None, reserved_input: int, reserved_output: int
    ) -> tuple[int, int]:
        if usage is None:
            return 0, 0
        if not (usage.total_tokens or usage.prompt_tokens or usage.completion_tokens):
            return reserved_input, reserved_output
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
    async def _mark_exhausted(session: AsyncSession, launch_id: UUID) -> None:
        await session.execute(
            text(
                "UPDATE planning_token_budgets SET status = 'EXHAUSTED', updated_at = now() "
                "WHERE launch_id = :launch_id"
            ),
            {"launch_id": launch_id},
        )
