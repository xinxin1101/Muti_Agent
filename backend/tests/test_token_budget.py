from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.agent import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentRole,
    LivenessCredit,
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
    PostgresPlanningTokenBudgetStore,
)
from app.persistence.token_budget import (
    BudgetDecisionFacts,
    PostgresRunTokenBudgetStore,
    RunBudgetExhaustedError,
    RunBudgetExhaustionFacts,
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


class _ProviderFailureDriver:
    async def complete(self, _request: AgentRequest) -> AgentResponse:
        raise TimeoutError("provider timed out")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _BudgetStore:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.reserved: tuple[int, int] | None = None
        self.has_progress: bool | None = None
        self.liveness_credit: LivenessCredit | None = None
        self.settled: TokenUsage | None = None
        self.observations: list[dict[str, object]] = []
        self.tool_outcomes: list[dict[str, object]] = []

    async def reserve(self, **kwargs):
        if self.blocked:
            raise TokenBudgetReservationError("本次运行模型预算已用尽，未向模型服务发起请求。")
        self.reserved = (kwargs["estimated_input_tokens"], kwargs["max_output_tokens"])
        self.has_progress = kwargs.get("has_progress")
        self.liveness_credit = kwargs.get("liveness_credit")
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


class _ExhaustedBudgetStore(_BudgetStore):
    async def reserve(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise RunBudgetExhaustedError(
            RunBudgetExhaustionFacts(
                total_budget_tokens=30_000,
                used_total_tokens=25_000,
                reserved_tokens=1_200,
                next_prompt_tokens=2_800,
                completion_limit_tokens=1_400,
            )
        )


class _CancelableBudgetStore(_BudgetStore):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled: TokenBudgetReservation | None = None

    async def cancel(self, reservation: TokenBudgetReservation) -> None:
        self.cancelled = reservation


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


class _PlanningLinkSession:
    def __init__(self) -> None:
        self.run_id = None
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        rendered = str(statement)
        values = dict(parameters)
        self.calls.append((rendered, values))
        if "SELECT run_id, used_prompt_tokens" in rendered:
            return _RowResult(
                _Row(
                    run_id=self.run_id,
                    used_prompt_tokens=700,
                    used_completion_tokens=300,
                    used_total_tokens=1_000,
                )
            )
        if "UPDATE planning_token_budgets SET run_id" in rendered:
            self.run_id = values["run_id"]
        return _RowResult(None)


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

    def one(self) -> _Row:
        assert self._row is not None
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
        if "FROM run_task_token_budgets" in rendered:
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
        if "FROM run_task_token_budgets" in rendered:
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
    assert store.liveness_credit is LivenessCredit.VERIFIED_PROGRESS
    assert store.settled == response.usage


@pytest.mark.anyio
async def test_budgeted_driver_marks_first_turn_as_initial_startup_liveness_credit() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(_request().model_copy(update={"execution_iteration": 1}))

    assert store.liveness_credit is LivenessCredit.INITIAL_STARTUP


@pytest.mark.anyio
async def test_budgeted_driver_forwards_explicit_tool_recovery_liveness_credit() -> None:
    store = _BudgetStore()
    driver = BudgetedAgentDriver(
        driver=_Driver(), budget_store=store, run_id=uuid4(), task_id="task-a"  # type: ignore[arg-type]
    )

    await driver.complete(
        _request().model_copy(
            update={"execution_iteration": 2, "liveness_credit": LivenessCredit.TOOL_RECOVERY}
        )
    )

    assert store.liveness_credit is LivenessCredit.TOOL_RECOVERY


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


@pytest.mark.anyio
async def test_run_hard_budget_rejection_exposes_reservation_aware_runtime_facts() -> None:
    driver = _Driver()
    guarded = BudgetedAgentDriver(
        driver=driver,
        budget_store=_ExhaustedBudgetStore(),  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-a",
    )

    with pytest.raises(AgentProviderError) as exc_info:
        await guarded.complete(_request())

    failure = exc_info.value.to_failure_report()
    assert driver.calls == 0
    assert failure.source.value == "runtime"
    assert "provider_called=false" in failure.evidence
    assert "run_total_budget_tokens=30000" in failure.evidence
    assert "run_remaining_tokens=3800" in failure.evidence
    assert "next_required_tokens=4200" in failure.evidence
    assert "budget_shortfall_tokens=400" in failure.evidence
    assert "prompt_context_floor_tokens=200" in failure.evidence
    assert "prompt_estimate_source=context_floor" in failure.evidence
    assert any(item.startswith("prompt_request_payload_tokens=") for item in failure.evidence)
    assert any(item.startswith("prompt_message_tokens=") for item in failure.evidence)
    assert any(item.startswith("prompt_tool_definition_tokens=") for item in failure.evidence)


@pytest.mark.anyio
async def test_provider_timeout_cancels_the_run_reservation() -> None:
    store = _CancelableBudgetStore()
    guarded = BudgetedAgentDriver(
        driver=_ProviderFailureDriver(),
        budget_store=store,  # type: ignore[arg-type]
        run_id=uuid4(),
        task_id="task-a",
    )

    with pytest.raises(TimeoutError):
        await guarded.complete(_request())

    assert store.reserved is not None
    assert store.cancelled is not None
    assert store.settled is None


def test_settlement_uses_actual_usage_and_releases_the_unused_reservation() -> None:
    reservation = TokenBudgetReservation(
        reservation_id=uuid4(),
        run_id=uuid4(),
        role=AgentRole.DEVELOPER,
        reserved_input_tokens=3_000,
        reserved_output_tokens=1_400,
    )
    store = object.__new__(PostgresRunTokenBudgetStore)

    prompt, completion = store._actual_usage(
        TokenUsage(prompt_tokens=1_100, completion_tokens=500, total_tokens=1_600),
        reservation,
    )

    assert (prompt, completion) == (1_100, 500)
    assert reservation.reserved_tokens - (prompt + completion) == 2_800


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
    assert stage_writes["DEVELOPMENT"] == 30_000
    assert stage_writes["REVIEW_PUBLICATION"] == 0
    assert stage_writes["FLEX"] == 0


def test_budget_plan_keeps_work_package_shares_non_blocking() -> None:
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

    store.validate_hierarchy_plan(
        dag=TaskDAG(tasks=tuple(package(f"package-{index}") for index in range(5))),
        developer_max_output_tokens=1_400,
    )


def test_legacy_package_borrow_facts_are_not_admission_controls() -> None:
    assert (
        PostgresRunTokenBudgetStore._borrow_denial_reason(
        has_progress=False,
        borrow_count=0,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
        )
        is None
    )
    assert PostgresRunTokenBudgetStore._borrow_denial_reason(
        has_progress=True,
        borrow_count=3,
        needed=1_200,
        flex_available=6_000,
        budget=8_000,
        maximum_budget=12_000,
    ) is None


@pytest.mark.anyio
async def test_soft_share_never_blocks_a_complex_work_package() -> None:
    """The old 12K/18K ceilings are telemetry; Run reservation is the hard gate."""

    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _BorrowingSession()
    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(), task_id="gomoku-core", role=AgentRole.DEVELOPER,
        estimated_input_tokens=8_000, max_output_tokens=2_000, required=10_000,
        has_progress=True,
    )

    assert not any("run_task_budget_loans" in statement for statement, _ in session.calls)
    assert not any(
        "developer_budget_tokens = developer_budget_tokens +" in statement
        for statement, _ in session.calls
    )


@pytest.mark.anyio
async def test_soft_share_records_liveness_without_turning_it_into_admission_control() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _BorrowingSession()
    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(), task_id="gomoku-core", role=AgentRole.DEVELOPER,
        estimated_input_tokens=4_081, max_output_tokens=1_400, required=5_481,
        has_progress=False, liveness_credit=LivenessCredit.CHECKPOINT_RESUME,
    )

    update = next(
        values for statement, values in session.calls if "last_liveness_credit" in statement
    )
    assert update["liveness_credit"] == LivenessCredit.CHECKPOINT_RESUME.value


@pytest.mark.anyio
async def test_legacy_borrow_count_and_ceiling_cannot_reject_new_reservations() -> None:
    store = object.__new__(PostgresRunTokenBudgetStore)
    session = _DependencyLendingSession()
    session._package.borrow_count = 99
    session._package.developer_budget_tokens = 1
    session._package.developer_used_tokens = 1
    await store._reserve_hierarchy(
        session=session,  # type: ignore[arg-type]
        run_id=uuid4(), task_id="core", role=AgentRole.DEVELOPER,
        estimated_input_tokens=20_000, max_output_tokens=2_000, required=22_000,
        has_progress=True,
    )

    assert not any("PACKAGE_CEILING" in str(values) for _, values in session.calls)


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


@pytest.mark.anyio
async def test_planner_usage_links_to_a_run_exactly_once_after_response_retry() -> None:
    session = _PlanningLinkSession()
    store = object.__new__(PostgresPlanningTokenBudgetStore)
    store._session_factory = _HierarchySessionFactory(session)  # type: ignore[assignment]
    launch_id = uuid4()
    run_id = uuid4()

    first = await store.link_to_run(launch_id=launch_id, run_id=run_id)
    repeated = await store.link_to_run(launch_id=launch_id, run_id=run_id)

    assert first == TokenUsage(prompt_tokens=700, completion_tokens=300, total_tokens=1_000)
    assert repeated == TokenUsage()
