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
from app.models.tools import ToolCall, ToolDefinition, ToolExecutionResult
from app.models.work_package import PlanningComplexity, TaskBudgetAllocation
from app.persistence.planning_budget import (
    PlanningTokenBudgetReservation,
    PlanningTokenBudgetReservationError,
)
from app.persistence.token_budget import (
    BudgetDecisionFacts,
    PostgresRunTokenBudgetStore,
    TokenBudgetPlanError,
    TokenBudgetReservation,
    TokenBudgetReservationError,
    WorkPackageBudgetAllocationError,
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
        self.allow_initial_credit: bool | None = None
        self.settled: TokenUsage | None = None
        self.observations: list[dict[str, object]] = []
        self.tool_outcomes: list[dict[str, object]] = []

    async def reserve(self, **kwargs):
        if self.blocked:
            raise TokenBudgetReservationError("本次运行模型预算已用尽，未向模型服务发起请求。")
        self.reserved = (kwargs["estimated_input_tokens"], kwargs["max_output_tokens"])
        self.has_progress = kwargs.get("has_progress")
        self.allow_initial_credit = kwargs.get("allow_initial_credit")
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

    async def record_model_turn_observation(self, **kwargs):  # type: ignore[no-untyped-def]
        self.observations.append(dict(kwargs))
        return 1

    async def record_tool_outcome_observation(self, **kwargs):  # type: ignore[no-untyped-def]
        self.tool_outcomes.append(dict(kwargs))


class _PlanningBudgetStore:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.reserved: tuple[int, int] | None = None
        self.settled: TokenUsage | None = None
        self.capacity: tuple[int, int] | None = None

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

    async def ensure_capacity(self, **kwargs) -> None:
        if self.blocked:
            raise PlanningTokenBudgetReservationError("启动规划预算不足")
        self.capacity = (kwargs["required_tokens"], kwargs["required_calls"])


class _HierarchyResult:
    def one_or_none(self):
        return None


class _HierarchySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        self.calls.append((str(statement), dict(parameters)))
        return _HierarchyResult()


class _HierarchySessionFactory:
    def __init__(self, session: _HierarchySession) -> None:
        self._session = session

    def begin(self) -> _HierarchySessionFactory:
        return self

    async def __aenter__(self) -> _HierarchySession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Row:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)


class _RowResult:
    def __init__(self, row: _Row | None) -> None:
        self._row = row

    def one_or_none(self) -> _Row | None:
        return self._row


class _BorrowingSession:
    """Minimal SQL boundary fake for the atomic FLEX borrowing branch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._development = _Row(
            total_budget_tokens=11_888, used_tokens=3_617, reserved_tokens=0
        )
        self._flex = _Row(total_budget_tokens=9_115, used_tokens=0, reserved_tokens=0)
        self._package = _Row(
            complexity="MEDIUM",
            developer_budget_tokens=5_794,
            repair_budget_tokens=1_497,
            developer_used_tokens=3_617,
            repair_used_tokens=0,
            developer_reserved_tokens=0,
            repair_reserved_tokens=0,
            borrow_count=0,
        )

    async def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        rendered = str(statement)
        values = dict(parameters)
        self.calls.append((rendered, values))
        if "SELECT total_budget_tokens, used_tokens, reserved_tokens FROM run_stage" in rendered:
            return _RowResult(
                self._flex if values["stage"] == "FLEX" else self._development
            )
        if "SELECT complexity, developer_budget_tokens" in rendered:
            return _RowResult(self._package)
        return _RowResult(None)


class _CalibrationSession:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, _statement, _parameters):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return _RowResult(_Row(developer_observed_prompt_tokens=4_800))
        return _RowResult(None)


class _RowsResult(_RowResult):
    def __init__(self, rows: tuple[_Row, ...]) -> None:
        super().__init__(rows[0] if rows else None)
        self._rows = rows

    def all(self) -> tuple[_Row, ...]:
        return self._rows


class _DependencyLendingSession:
    """Budget snapshot with a blocked direct dependant and protected startup pool."""

    def __init__(self, *, protect_lender: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._development = _Row(
            total_budget_tokens=20_000, used_tokens=1_000, reserved_tokens=0
        )
        self._flex = _Row(total_budget_tokens=1_000, used_tokens=0, reserved_tokens=0)
        self._package = _Row(
            complexity="MEDIUM",
            developer_budget_tokens=11_500,
            repair_budget_tokens=1_000,
            developer_used_tokens=1_000,
            repair_used_tokens=0,
            developer_reserved_tokens=0,
            repair_reserved_tokens=0,
            developer_startup_reserve_tokens=0,
            complexity_upgrade_count=0,
            borrow_count=0,
        )
        self._lender = _Row(
            task_id="ui",
            developer_budget_tokens=7_000,
            developer_used_tokens=0,
            developer_reserved_tokens=0,
            developer_startup_reserve_tokens=7_000 if protect_lender else 4_000,
        )

    async def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        rendered = str(statement)
        values = dict(parameters)
        self.calls.append((rendered, values))
        if "FROM run_task_token_budgets AS budget" in rendered:
            return _RowsResult((self._lender,))
        if "SELECT total_budget_tokens, used_tokens, reserved_tokens FROM run_stage" in rendered:
            return _RowResult(self._flex if values["stage"] == "FLEX" else self._development)
        if "SELECT complexity, developer_budget_tokens" in rendered:
            return _RowResult(self._package)
        return _RowResult(None)


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
    assert store.allow_initial_credit is False
    assert store.settled == response.usage


@pytest.mark.anyio
async def test_budgeted_driver_allows_only_first_turn_to_use_run_start_credit() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(_request().model_copy(update={"execution_iteration": 1}))

    assert store.allow_initial_credit is True


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
async def test_budgeted_driver_records_cost_observation_and_tool_result_size() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(_request().model_copy(update={"execution_iteration": 2}))
    await driver.record_tool_outcome(
        role=AgentRole.DEVELOPER.value,
        results=[
            ToolExecutionResult(
                tool_call_id="read-1",
                name="read_range",
                ok=True,
                content="x" * 2_000,
            )
        ],
        has_real_progress=True,
    )

    assert store.observations[0]["iteration"] == 2
    assert store.observations[0]["usage"].prompt_tokens == 12
    assert store.tool_outcomes[0]["tool_result_tokens"] > 500
    assert store.tool_outcomes[0]["has_real_progress"] is True


@pytest.mark.anyio
async def test_budgeted_driver_excludes_compacted_write_arguments_from_next_prediction() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(_request().model_copy(update={"execution_iteration": 2}))
    call = ToolCall(
        id="write-1",
        name="write_file",
        arguments='{"path":"app/game.py","content":"' + ("x" * 8_000) + '"}',
    )
    await driver.record_tool_outcome(
        role=AgentRole.DEVELOPER.value,
        calls=[call],
        results=[
            ToolExecutionResult(
                tool_call_id="write-1",
                name="write_file",
                ok=True,
                content='{"path":"app/game.py","status":"written"}',
            )
        ],
        has_real_progress=True,
        compacted_code_mutation=True,
    )

    assert store.tool_outcomes[0]["compacted_tool_argument_tokens"] > 2_000


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


@pytest.mark.anyio
async def test_hierarchy_initialization_unpacks_every_work_package_allocation() -> None:
    def package(task_id: str) -> TaskNode:
        return TaskNode(
            task=TaskContract(
                task_id=task_id,
                objective="实现独立模块。",
                writable_files=(f"src/{task_id}.py",),
                acceptance_criteria=("模块可验证。",),
                verification_commands=("pytest -q",),
            ),
            complexity=PlanningComplexity.MEDIUM,
            budget_allocation=TaskBudgetAllocation(
                package_id=task_id,
                recommended_token_budget=4_000,
            ),
        )

    session = _HierarchySession()
    store = object.__new__(PostgresRunTokenBudgetStore)
    store._default_total_budget_tokens = 30_000
    store._session_factory = _HierarchySessionFactory(session)  # type: ignore[assignment]

    await store.initialize_hierarchy(
        run_id=uuid4(),
        dag=TaskDAG(tasks=(package("core"), package("ui"), package("integration"))),
    )

    task_budget_writes = [
        parameters
        for statement, parameters in session.calls
        if "INSERT INTO run_task_token_budgets" in statement
    ]
    assert [item["task_id"] for item in task_budget_writes] == ["core", "ui", "integration"]
    stage_writes = {
        parameters["stage"]: parameters["total"]
        for statement, parameters in session.calls
        if "INSERT INTO run_stage_token_budgets" in statement
    }
    # Planning is settled before a Run exists. Its former Run-stage share must be
    # immediately usable by the active critical path rather than stranded here.
    assert stage_writes["PLANNING"] == 0
    assert stage_writes["REVIEW_PUBLICATION"] <= 1_000
    assert stage_writes["FLEX"] > 0


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
        borrow_count=0,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
    )
    assert reason == "本轮前没有可验证的代码或工具进展。"
    assert PostgresRunTokenBudgetStore._borrow_denial_reason(
        has_progress=True,
        borrow_count=3,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
    ) == "已达到该工作包的 3 次 FLEX 借款上限。"


@pytest.mark.anyio
async def test_flex_borrows_immediately_when_next_call_does_not_fit_and_task_progressed() -> None:
    """Regression for 3617 used / 2177 left / 5481 required / 9115 FLEX."""

    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _BorrowingSession()

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="gomoku-core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=4_081,
        max_output_tokens=1_400,
        required=5_481,
        has_progress=True,
    )

    flex_updates = [
        values
        for statement, values in session.calls
        if (
            "UPDATE run_stage_token_budgets SET total_budget_tokens = total_budget_tokens -"
            in statement
        )
    ]
    package_updates = [
        values
        for statement, values in session.calls
        if "developer_budget_tokens = developer_budget_tokens + :needed" in statement
    ]
    assert len(flex_updates) == 1
    assert flex_updates[0]["stage"] == "FLEX"
    assert flex_updates[0]["amount"] == 3_304
    assert package_updates[0]["needed"] == 3_304


@pytest.mark.anyio
async def test_first_turn_can_use_run_credit_before_any_tool_progress() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _BorrowingSession()

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="gomoku-core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=4_081,
        max_output_tokens=1_400,
        required=5_481,
        has_progress=False,
        allow_initial_credit=True,
    )

    decision = next(
        values
        for statement, values in session.calls
        if "INSERT INTO run_token_budget_decisions" in statement
    )
    assert "首次受控开发调用使用 Run 启动信用" in str(decision["reason"])


@pytest.mark.anyio
async def test_work_package_reservation_without_borrowing_records_zero_downstream() -> None:
    """A first call that fits the package budget must not read an uninitialized lender value."""

    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _BorrowingSession()

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="gomoku-core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=600,
        max_output_tokens=400,
        required=1_000,
        has_progress=False,
    )

    snapshot_write = next(
        values
        for statement, values in session.calls
        if "last_downstream_available_tokens" in statement
    )
    assert snapshot_write["downstream"] == 0
    assert snapshot_write["borrowed"] == 0


@pytest.mark.anyio
async def test_dependency_blocked_lender_can_supply_only_capacity_above_startup_pool() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _DependencyLendingSession()

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=12_600,
        max_output_tokens=1_400,
        required=14_000,
        has_progress=True,
    )

    loan_writes = [
        values
        for statement, values in session.calls
        if "INSERT INTO run_task_budget_loans" in statement
    ]
    upgrade_writes = [
        values
        for statement, values in session.calls
        if "complexity_upgrade_count = complexity_upgrade_count + :upgrade" in statement
    ]
    assert loan_writes == [
        {
            "run_id": loan_writes[0]["run_id"],
            "borrower": "core",
            "lender": "ui",
            "role": "developer",
            "amount": 2_500,
        }
    ]
    assert upgrade_writes[0]["complexity"] == "HIGH"
    assert upgrade_writes[0]["upgrade"] == 1


@pytest.mark.anyio
async def test_high_critical_path_package_can_expand_once_without_independent_budget() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _DependencyLendingSession()
    session._package.complexity = "HIGH"
    session._package.developer_budget_tokens = 14_049
    session._package.developer_used_tokens = 11_805
    session._package.borrow_count = 2
    session._flex.total_budget_tokens = 4_845
    session._lender.developer_budget_tokens = 6_106
    session._lender.developer_startup_reserve_tokens = 3_053

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="gomoku-core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=6_959,
        max_output_tokens=2_048,
        required=9_007,
        has_progress=True,
    )

    package_update = next(
        values
        for statement, values in session.calls
        if "developer_budget_tokens = developer_budget_tokens + :needed" in statement
    )
    loan = next(
        values
        for statement, values in session.calls
        if "INSERT INTO run_task_budget_loans" in statement
    )
    assert package_update["needed"] == 6_763
    assert loan["amount"] == 1_918


@pytest.mark.anyio
async def test_blocked_lender_startup_pool_is_preserved_and_run_credit_continues() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _DependencyLendingSession(protect_lender=True)

    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=12_600,
        max_output_tokens=1_400,
        required=14_000,
        has_progress=True,
    )

    assert not any(
        "INSERT INTO run_task_budget_loans" in statement for statement, _ in session.calls
    )
    decision = next(
        values
        for statement, values in session.calls
        if "INSERT INTO run_token_budget_decisions" in statement
    )
    assert "Run 总预算信用流动" in str(decision["reason"])


@pytest.mark.anyio
async def test_denied_budget_snapshot_is_persisted_in_a_separate_transaction() -> None:
    session = _HierarchySession()
    store = object.__new__(PostgresRunTokenBudgetStore)
    store._session_factory = _HierarchySessionFactory(session)  # type: ignore[assignment]
    facts = BudgetDecisionFacts(
        run_id=uuid4(),
        task_id="core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=6_815,
        max_output_tokens=1_400,
        required_tokens=8_215,
        package_available_tokens=892,
        flex_available_tokens=5_031,
        downstream_available_tokens=0,
        borrowed_tokens=0,
        decision="DENIED",
        reason="FLEX 与可延期下游预算均不足；独立或最低启动预算未被动用。",
        limit_flags=("DEPENDENT_DOWNSTREAM_RESERVE",),
        recovery_action="continue_from_checkpoint",
    )

    await store._persist_budget_decision_independently(facts)

    decision_write = next(
        values
        for statement, values in session.calls
        if "INSERT INTO run_token_budget_decisions" in statement
    )
    snapshot_write = next(
        values
        for statement, values in session.calls
        if "last_downstream_available_tokens" in statement
    )
    assert decision_write["required"] == 8_215
    assert decision_write["flex"] == 5_031
    assert decision_write["downstream"] == 0
    assert snapshot_write["recovery"] == "continue_from_checkpoint"


@pytest.mark.anyio
async def test_next_reservation_uses_observed_prompt_floor_with_safety_margin() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    store._token_estimate_safety_factor = 1.15

    calibrated = await store._calibrated_input_estimate(
        session=_CalibrationSession(),  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="gomoku-core",
        role=AgentRole.DEVELOPER,
        estimated_input_tokens=4_000,
    )

    assert calibrated == 5_520


def test_next_turn_prediction_reserves_for_large_write_or_tool_result_growth() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    store._token_estimate_safety_factor = 1.15

    predicted = store._next_input_prediction(
        current_request_estimate=3_700,
        observed_prompt_tokens=3_700,
        context_growth_tokens=500,
        tool_argument_tokens=3_600,
        tool_result_tokens=400,
        write_patch_argument_tokens=3_600,
    )

    assert predicted >= 8_000


def test_next_turn_prediction_drops_a_successfully_compacted_write_payload() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    store._token_estimate_safety_factor = 1.15

    predicted = store._next_input_prediction(
        current_request_estimate=3_800,
        observed_prompt_tokens=3_800,
        context_growth_tokens=300,
        tool_argument_tokens=0,
        tool_result_tokens=80,
        write_patch_argument_tokens=0,
    )

    # The original write payload may be several thousand Tokens, but it is absent
    # from the next prompt after deterministic code-mutation compaction.
    assert predicted < 5_000


@pytest.mark.anyio
async def test_work_package_allocation_block_is_runtime_failure_and_remains_recoverable() -> None:
    class _AllocationBlockedStore(_BudgetStore):
        async def reserve(self, **_kwargs):  # type: ignore[no-untyped-def]
            raise WorkPackageBudgetAllocationError("FLEX 弹性池余额不足。")

    guarded = BudgetedAgentDriver(
        driver=_Driver(),
        budget_store=_AllocationBlockedStore(),  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-a",
    )

    with pytest.raises(AgentProviderError) as exc_info:
        await guarded.complete(_request())

    report = exc_info.value.to_failure_report()
    assert report.failure_type is FailureType.WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED
    assert report.source.value == "runtime"
    assert report.retryable is False


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


@pytest.mark.anyio
async def test_planning_driver_preflights_all_bounded_calls_before_provider_contact() -> None:
    store = _PlanningBudgetStore()
    guarded = PlanningBudgetedAgentDriver(
        driver=_Driver(),
        budget_store=store,  # type: ignore[arg-type]
        launch_id=uuid4(),
    )
    request = _request().model_copy(update={"role": AgentRole.PLANNER})

    await guarded.ensure_capacity((request, request.model_copy(update={"max_output_tokens": 300})))

    assert store.capacity == (1_100, 2)


@pytest.mark.anyio
async def test_planning_driver_preflight_rejects_before_any_provider_contact() -> None:
    driver = _Driver()
    guarded = PlanningBudgetedAgentDriver(
        driver=driver,
        budget_store=_PlanningBudgetStore(blocked=True),  # type: ignore[arg-type]
        launch_id=uuid4(),
    )

    with pytest.raises(AgentProviderError) as exc_info:
        await guarded.ensure_capacity((_request().model_copy(update={"role": AgentRole.PLANNER}),))

    assert driver.calls == 0
    assert exc_info.value.to_failure_report().failure_type is FailureType.TOKEN_BUDGET_EXHAUSTED
