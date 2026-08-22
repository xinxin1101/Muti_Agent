from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.models.dispatch_attempt import DispatchAttemptState, PersistedDispatchAttempt
from app.models.lease import TaskLeaseState
from app.models.operator_recovery import (
    OperatorActionExecutionResult,
    OperatorActionKind,
    OperatorRecoveryPlan,
)
from app.models.run_reconciliation import (
    DAGRunReconciliationPlan,
    DAGTaskFrontierState,
    DAGTaskReconciliationRecord,
    TaskExecutionBase,
    TaskExecutionBaseBasis,
)
from app.runtime.operator_recovery import (
    OperatorActionStaleError,
    OperatorRecoveryCoordinator,
    OperatorRecoveryPlanner,
)

_BASE = "a" * 40
_OBSERVED = datetime(2026, 8, 22, 6, 0, tzinfo=UTC)


def _plan(
    *,
    run_id: UUID | None = None,
    task_count: int = 1,
    state: DAGTaskFrontierState = DAGTaskFrontierState.RECONCILE_CANDIDATE,
    run_status: str = "RUNNING",
    observed_at: datetime = _OBSERVED,
) -> DAGRunReconciliationPlan:
    selected_run_id = run_id or uuid4()
    tasks = []
    task_ids = tuple(chr(ord("A") + index) for index in range(task_count))
    for index, task_id in enumerate(task_ids):
        execution_base = None
        lease_state = TaskLeaseState.UNOWNED
        lease_generation = 0
        lease_dispatch_id = None
        if state is DAGTaskFrontierState.RECONCILE_CANDIDATE:
            lease_state = TaskLeaseState.EXPIRED
            lease_generation = 1
            lease_dispatch_id = uuid4()
            execution_base = TaskExecutionBase(
                run_id=selected_run_id,
                task_id=task_id,
                commit_sha=_BASE,
                basis=TaskExecutionBaseBasis.RUN_BASE,
            )
        elif state is DAGTaskFrontierState.WAIT_ACTIVE_OWNER:
            lease_state = TaskLeaseState.ACTIVE
            lease_generation = 1
            lease_dispatch_id = uuid4()
        tasks.append(
            DAGTaskReconciliationRecord(
                run_id=selected_run_id,
                task_id=task_id,
                topological_index=index,
                frontier_state=state,
                lease_state=lease_state,
                lease_generation=lease_generation,
                lease_dispatch_id=lease_dispatch_id,
                execution_base=execution_base,
                reason="test frontier",
            )
        )
    reconcile_ids = task_ids if state is DAGTaskFrontierState.RECONCILE_CANDIDATE else ()
    return DAGRunReconciliationPlan(
        run_id=selected_run_id,
        run_status=run_status,  # type: ignore[arg-type]
        dag_sha256="b" * 64,
        topology_source="PERSISTED",
        observed_at=observed_at,
        topological_order=task_ids,
        ready_task_ids=task_ids if run_status == "RUNNING" else (),
        reconcile_task_ids=reconcile_ids if run_status == "RUNNING" else (),
        tasks=tuple(tasks),
    )


def _attempt(
    *,
    run_id: UUID,
    task_id: str,
    dispatch_id: UUID | None = None,
    attempt_number: int = 1,
    state: DispatchAttemptState = DispatchAttemptState.REQUESTED,
) -> PersistedDispatchAttempt:
    selected_dispatch = dispatch_id or uuid4()
    if state is DispatchAttemptState.ENQUEUED:
        return PersistedDispatchAttempt(
            dispatch_id=selected_dispatch,
            run_id=run_id,
            task_id=task_id,
            attempt_number=attempt_number,
            state=state,
            broker_message_id=f"broker-{attempt_number}",
            queue_name="devflow_tasks",
            requested_at=_OBSERVED,
            resolved_at=_OBSERVED,
            updated_at=_OBSERVED,
        )
    return PersistedDispatchAttempt(
        dispatch_id=selected_dispatch,
        run_id=run_id,
        task_id=task_id,
        attempt_number=attempt_number,
        state=state,
        requested_at=_OBSERVED,
        updated_at=_OBSERVED,
    )


def test_action_id_ignores_observation_time_but_binds_dispatch_ledger() -> None:
    run_id = uuid4()
    first_plan = _plan(run_id=run_id, observed_at=_OBSERVED)
    second_plan = first_plan.model_copy(
        update={"observed_at": datetime(2026, 8, 22, 6, 5, tzinfo=UTC)}
    )
    empty = {"A": ()}

    first = OperatorRecoveryPlanner._advance_action(first_plan, dispatches=empty)
    second = OperatorRecoveryPlanner._advance_action(second_plan, dispatches=empty)
    after_dispatch = OperatorRecoveryPlanner._advance_action(
        second_plan,
        dispatches={"A": (_attempt(run_id=run_id, task_id="A"),)},
    )

    assert first.action_id == second.action_id
    assert after_dispatch.action_id != first.action_id


def test_active_owner_and_terminal_run_never_advertise_operator_mutation() -> None:
    active = _plan(state=DAGTaskFrontierState.WAIT_ACTIVE_OWNER)
    terminal = _plan(
        state=DAGTaskFrontierState.RUN_TERMINAL,
        run_status="SUCCEEDED",
    )

    assert OperatorRecoveryPlanner._should_offer_advance(active, dispatches={"A": ()}) is False
    assert OperatorRecoveryPlanner._should_offer_advance(terminal, dispatches={"A": ()}) is False


def test_newer_unclaimed_dispatch_suppresses_another_operator_advance() -> None:
    plan = _plan()
    task = plan.tasks[0]
    assert task.lease_dispatch_id is not None
    current = _attempt(
        run_id=plan.run_id,
        task_id=task.task_id,
        dispatch_id=task.lease_dispatch_id,
        attempt_number=1,
        state=DispatchAttemptState.ENQUEUED,
    )
    newer = _attempt(
        run_id=plan.run_id,
        task_id=task.task_id,
        attempt_number=2,
        state=DispatchAttemptState.REQUESTED,
    )

    assert (
        OperatorRecoveryPlanner._should_offer_advance(
            plan,
            dispatches={task.task_id: (current,)},
        )
        is True
    )
    assert (
        OperatorRecoveryPlanner._should_offer_advance(
            plan,
            dispatches={task.task_id: (current, newer)},
        )
        is False
    )


class _PlanSource:
    def __init__(self, plans: list[OperatorRecoveryPlan]) -> None:
        self._plans = plans
        self.calls = 0

    async def build_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        selected = self._plans[min(self.calls, len(self._plans) - 1)]
        self.calls += 1
        assert selected.run_id == run_id
        return selected


class _AuditStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict] = []

    async def append_evidence(self, **kwargs) -> int:
        self.events.append("audit")
        self.calls.append(kwargs)
        return 7


class _Controller:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def advance(self, run_id: UUID) -> None:
        self.events.append("controller")
        self.calls += 1


class _Reconciler:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    async def reconcile_run(self, run_id: UUID) -> None:
        self.events.append("reconciler")
        self.calls += 1


def _operator_plan(reconciliation: DAGRunReconciliationPlan) -> OperatorRecoveryPlan:
    action = OperatorRecoveryPlanner._advance_action(
        reconciliation,
        dispatches={task_id: () for task_id in reconciliation.topological_order},
    )
    return OperatorRecoveryPlan(
        run_id=reconciliation.run_id,
        reconciliation=reconciliation,
        actions=(action,),
    )


def test_stale_action_is_rejected_before_audit_or_runtime_mutation() -> None:
    asyncio.run(_stale_action_is_rejected_before_audit_or_runtime_mutation())


async def _stale_action_is_rejected_before_audit_or_runtime_mutation() -> None:
    reconciliation = _plan()
    plan = OperatorRecoveryPlan(run_id=reconciliation.run_id, reconciliation=reconciliation)
    events: list[str] = []
    audit = _AuditStore(events)
    controller = _Controller(events)
    reconciler = _Reconciler(events)
    coordinator = OperatorRecoveryCoordinator(
        planner=_PlanSource([plan]),  # type: ignore[arg-type]
        audit_store=audit,
        run_controller=controller,
        run_reconciler=reconciler,
    )

    with pytest.raises(OperatorActionStaleError):
        await coordinator.execute(run_id=reconciliation.run_id, action_id="c" * 64)

    assert events == []
    assert audit.calls == []
    assert controller.calls == 0
    assert reconciler.calls == 0


def test_single_task_operator_action_audits_before_reconciliation() -> None:
    asyncio.run(_single_task_operator_action_audits_before_reconciliation())


async def _single_task_operator_action_audits_before_reconciliation() -> None:
    plan = _operator_plan(_plan(task_count=1))
    events: list[str] = []
    audit = _AuditStore(events)
    controller = _Controller(events)
    reconciler = _Reconciler(events)
    coordinator = OperatorRecoveryCoordinator(
        planner=_PlanSource([plan, plan]),  # type: ignore[arg-type]
        audit_store=audit,
        run_controller=controller,
        run_reconciler=reconciler,
    )

    result = await coordinator.execute(run_id=plan.run_id, action_id=plan.actions[0].action_id)

    assert isinstance(result, OperatorActionExecutionResult)
    assert result.action.kind is OperatorActionKind.ADVANCE_RUN
    assert result.request_evidence_id == 7
    assert events == ["audit", "reconciler"]
    assert controller.calls == 0
    assert audit.calls[0]["evidence_key"] == f"operator:request:{plan.actions[0].action_id}"


def test_multi_task_operator_action_delegates_to_durable_controller() -> None:
    asyncio.run(_multi_task_operator_action_delegates_to_durable_controller())


async def _multi_task_operator_action_delegates_to_durable_controller() -> None:
    plan = _operator_plan(_plan(task_count=2))
    events: list[str] = []
    audit = _AuditStore(events)
    controller = _Controller(events)
    reconciler = _Reconciler(events)
    coordinator = OperatorRecoveryCoordinator(
        planner=_PlanSource([plan, plan]),  # type: ignore[arg-type]
        audit_store=audit,
        run_controller=controller,
        run_reconciler=reconciler,
    )

    await coordinator.execute(run_id=plan.run_id, action_id=plan.actions[0].action_id)

    assert events == ["audit", "controller"]
    assert controller.calls == 1
    assert reconciler.calls == 0
