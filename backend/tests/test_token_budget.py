from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.agent import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentRole,
    MessageRole,
    TokenUsage,
)
from app.models.dag import TaskDAG, TaskNode
from app.models.failure import FailureType
from app.models.task import TaskContract
from app.models.tools import ToolCall, ToolDefinition
from app.models.work_package import PlanningComplexity, TaskBudgetAllocation
from app.persistence.planning_budget import (
    PlanningTokenBudgetReservation,
    PlanningTokenBudgetReservationError,
)
from app.persistence.token_budget import (
    PostgresRunTokenBudgetStore,
    TokenBudgetPlanError,
    TokenBudgetReservation,
    TokenBudgetReservationError,
)
from app.providers.budgeted import BudgetedAgentDriver
from app.providers.errors import AgentProviderError
from app.providers.planning_budgeted import PlanningBudgetedAgentDriver


class _Driver:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: AgentRequest) -> AgentResponse:
        self.calls += 1
        return AgentResponse(
            model=request.model,
            content="done",
            usage=TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
            latency_ms=1,
        )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _BudgetStore:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.reserved: tuple[int, int] | None = None
        self.has_progress: bool | None = None
        self.settled: TokenUsage | None = None

    async def reserve(self, **kwargs):
        if self.blocked:
            raise TokenBudgetReservationError("本次运行模型预算已用尽，未向模型服务发起请求。")
        self.reserved = (kwargs["estimated_input_tokens"], kwargs["max_output_tokens"])
        self.has_progress = kwargs.get("has_progress")
        return TokenBudgetReservation(
            reservation_id=uuid4(),
            run_id=kwargs["run_id"],
            role=kwargs["role"],
            reserved_input_tokens=kwargs["estimated_input_tokens"],
            reserved_output_tokens=kwargs["max_output_tokens"],
        )

    async def settle(self, _reservation: TokenBudgetReservation, usage: TokenUsage) -> None:
        self.settled = usage

    async def cancel(self, _reservation: TokenBudgetReservation) -> None:
        raise AssertionError("successful request must not cancel its reservation")


class _PlanningBudgetStore:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.reserved: tuple[int, int] | None = None
        self.settled: TokenUsage | None = None

    async def reserve(self, **kwargs):
        if self.blocked:
            raise PlanningTokenBudgetReservationError("本次启动的规划模型预算已用尽")
        self.reserved = (kwargs["estimated_input_tokens"], kwargs["max_output_tokens"])
        return PlanningTokenBudgetReservation(
            reservation_id=uuid4(),
            launch_id=kwargs["launch_id"],
            reserved_input_tokens=kwargs["estimated_input_tokens"],
            reserved_output_tokens=kwargs["max_output_tokens"],
        )

    async def settle(self, _reservation: PlanningTokenBudgetReservation, usage: TokenUsage) -> None:
        self.settled = usage

    async def cancel(self, _reservation: PlanningTokenBudgetReservation) -> None:
        raise AssertionError("successful planner request must not cancel its reservation")


def _request() -> AgentRequest:
    return AgentRequest(
        role=AgentRole.DEVELOPER,
        model="example/model",
        messages=[AgentMessage(role=MessageRole.USER, content="implement one small feature")],
        context_estimated_tokens=200,
        max_output_tokens=400,
        budget_progress=True,
    )


@pytest.mark.anyio
async def test_budgeted_driver_reserves_before_call_and_settles_actual_usage() -> None:
    store = _BudgetStore()
    driver = _Driver()
    guarded = BudgetedAgentDriver(
        driver=driver,
        budget_store=store,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-a",
    )

    response = await guarded.complete(_request())

    assert driver.calls == 1
    assert store.reserved == (200, 400)
    assert store.has_progress is True
    assert store.settled == response.usage


@pytest.mark.anyio
async def test_budgeted_driver_reserves_large_tool_call_arguments() -> None:
    store = _BudgetStore()
    request = AgentRequest(
        role=AgentRole.DEVELOPER,
        model="example/model",
        messages=[
            AgentMessage(
                role=MessageRole.ASSISTANT,
                tool_calls=[ToolCall(id="write", name="write_file", arguments="x" * 20_000)],
            )
        ],
        max_output_tokens=400,
        tools=[
            ToolDefinition(
                name="write_file",
                description="write source",
                parameters={"type": "object", "properties": {"content": {"type": "string"}}},
            )
        ],
    )
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(request)

    assert store.reserved is not None
    assert store.reserved[0] > 5_000


@pytest.mark.anyio
async def test_budgeted_driver_does_not_contact_provider_when_reservation_is_rejected() -> None:
    store = _BudgetStore(blocked=True)
    driver = _Driver()
    guarded = BudgetedAgentDriver(
        driver=driver,
        budget_store=store,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-a",
    )

    with pytest.raises(AgentProviderError) as exc_info:
        await guarded.complete(_request())

    assert driver.calls == 0
    assert exc_info.value.to_failure_report().failure_type is FailureType.TOKEN_BUDGET_EXHAUSTED


def test_medium_work_package_reserves_two_startup_turns_before_flex_borrowing() -> None:
    task = TaskContract(
        task_id="core",
        objective="实现棋盘领域模型和胜负规则。",
        writable_files=("gomoku/core.py",),
        acceptance_criteria=("支持五连胜负判定。",),
        verification_commands=("pytest tests/test_core.py -q",),
    )
    node = TaskNode(
        task=task,
        complexity=PlanningComplexity.MEDIUM,
        budget_allocation=TaskBudgetAllocation(
            package_id="core",
            recommended_token_budget=4_000,
        ),
    )
    store = object.__new__(PostgresRunTokenBudgetStore)

    _, developer, repair = store._initial_package_allocation(node, 1_400)

    assert developer >= 2 * (1_200 + 1_400)
    assert repair >= 800


def test_budget_plan_allows_three_medium_packages_with_flex_remaining() -> None:
    def package(task_id: str) -> TaskNode:
        return TaskNode(
            task=TaskContract(
                task_id=task_id,
                objective="实现复杂核心算法、交互界面和集成测试。",
                writable_files=("src/a.py", "src/b.py", "src/c.py"),
                acceptance_criteria=("交付可运行功能。",),
                verification_commands=("pytest -q",),
            ),
            complexity=PlanningComplexity.MEDIUM,
            budget_allocation=TaskBudgetAllocation(
                package_id=task_id,
                recommended_token_budget=4_000,
            ),
        )

    store = object.__new__(PostgresRunTokenBudgetStore)
    store._default_total_budget_tokens = 30_000
    dag = TaskDAG(tasks=(package("core"), package("ui"), package("integration")))

    store.validate_hierarchy_plan(dag=dag, developer_max_output_tokens=1_400)


def test_budget_plan_rejects_only_when_safe_startup_cannot_be_funded() -> None:
    def package(task_id: str) -> TaskNode:
        return TaskNode(
            task=TaskContract(
                task_id=task_id,
                objective="实现复杂核心算法、交互界面和集成测试。",
                writable_files=("src/a.py", "src/b.py", "src/c.py"),
                acceptance_criteria=("交付可运行功能。",),
                verification_commands=("pytest -q",),
            ),
            complexity=PlanningComplexity.HIGH,
            budget_allocation=TaskBudgetAllocation(
                package_id=task_id,
                recommended_token_budget=12_000,
            ),
        )

    store = object.__new__(PostgresRunTokenBudgetStore)
    store._default_total_budget_tokens = 30_000

    with pytest.raises(TokenBudgetPlanError, match="预算计划不可执行"):
        store.validate_hierarchy_plan(
            dag=TaskDAG(tasks=tuple(package(f"package-{index}") for index in range(5))),
            developer_max_output_tokens=1_400,
        )


def test_flex_borrow_denial_needs_progress_and_is_bounded() -> None:
    reason = PostgresRunTokenBudgetStore._borrow_denial_reason(
        has_progress=False,
        at_borrow_threshold=True,
        borrow_count=0,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
    )
    assert reason == "本轮前没有可验证的代码或工具进展。"
    assert PostgresRunTokenBudgetStore._borrow_denial_reason(
        has_progress=True,
        at_borrow_threshold=True,
        borrow_count=3,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
    ) == "已达到该工作包的 3 次 FLEX 借款上限。"


@pytest.mark.anyio
async def test_planning_driver_reserves_before_provider_and_settles_usage() -> None:
    store = _PlanningBudgetStore()
    driver = _Driver()
    guarded = PlanningBudgetedAgentDriver(
        driver=driver,
        budget_store=store,  # type: ignore[arg-type]
        launch_id=uuid4(),
    )
    request = _request().model_copy(update={"role": AgentRole.PLANNER})

    response = await guarded.complete(request)

    assert driver.calls == 1
    assert store.reserved == (200, 400)
    assert store.settled == response.usage


@pytest.mark.anyio
async def test_planning_driver_does_not_contact_provider_when_budget_rejects() -> None:
    driver = _Driver()
    guarded = PlanningBudgetedAgentDriver(
        driver=driver,
        budget_store=_PlanningBudgetStore(blocked=True),  # type: ignore[arg-type]
        launch_id=uuid4(),
    )

    with pytest.raises(AgentProviderError) as exc_info:
        await guarded.complete(_request().model_copy(update={"role": AgentRole.PLANNER}))

    assert driver.calls == 0
    assert exc_info.value.to_failure_report().failure_type is FailureType.TOKEN_BUDGET_EXHAUSTED
