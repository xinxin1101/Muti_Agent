from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import TaskContract, TaskDAG, TaskNode
from app.models.run_reconciliation import DAGTaskFrontierState
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.runtime import EvidenceBoundTaskExecutionBaseResolver, IdempotentTaskReconciler
from app.runtime.operator_recovery import (
    OperatorActionStaleError,
    OperatorRecoveryCoordinator,
    OperatorRecoveryPlanner,
)
from app.runtime.run_reconciler import DAGRunReconciler

_BASE = "a" * 40


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for operator recovery tests")
    pytest.skip("PostgreSQL operator recovery tests require DEVFLOW_DATABASE_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="A",
        objective="Recover one task through accepted authority.",
        readable_files=["src/**"],
        writable_files=["src/a.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Recovery remains durable and fenced."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


class _AckActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def send(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        return SimpleNamespace(message_id=f"operator-{self.calls}")


class _UnusedController:
    def __init__(self) -> None:
        self.calls = 0

    async def advance(self, run_id) -> None:
        self.calls += 1
        raise AssertionError("single-task operator recovery must use DAGRunReconciler")


async def _start_run(evidence_store: PostgresEvidenceStore, dag_store: PostgresDAGStore):
    project_id = await evidence_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/operator.git",
        default_branch="main",
    )
    return await dag_store.start_run(
        project_id=project_id,
        dag=TaskDAG(tasks=(TaskNode(task=_task(), depends_on=()),)),
        base_commit=_BASE,
    )


def test_active_owner_is_read_only_on_operator_surface() -> None:
    asyncio.run(_active_owner_is_read_only_on_operator_surface())


async def _active_owner_is_read_only_on_operator_surface() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    planner = OperatorRecoveryPlanner(
        run_reader=evidence_store,
        dag_reader=dag_store,
        lease_reader=lease_store,
        dispatch_reader=dispatch_store,
        execution_base_resolver=EvidenceBoundTaskExecutionBaseResolver(dag_reader=dag_store),
    )
    try:
        run_id = await _start_run(evidence_store, dag_store)
        dispatch_id = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id="A",
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id="A",
            broker_message_id="active-owner",
            queue_name="devflow_tasks",
        )
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id="A",
            owner_id="live-worker",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )

        plan = await planner.build_plan(run_id)

        assert plan.actions == ()
        assert plan.reconciliation.tasks[0].frontier_state is DAGTaskFrontierState.WAIT_ACTIVE_OWNER
    finally:
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


def test_concurrent_operator_actions_publish_once_and_make_old_action_stale() -> None:
    asyncio.run(_concurrent_operator_actions_publish_once_and_make_old_action_stale())


async def _concurrent_operator_actions_publish_once_and_make_old_action_stale() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    task_reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    execution_base_resolver = EvidenceBoundTaskExecutionBaseResolver(dag_reader=dag_store)
    run_reconciler = DAGRunReconciler(
        run_reader=evidence_store,
        dag_reader=dag_store,
        lease_reader=lease_store,
        task_reconciler=task_reconciler,
        execution_base_resolver=execution_base_resolver,
    )
    planner = OperatorRecoveryPlanner(
        run_reader=evidence_store,
        dag_reader=dag_store,
        lease_reader=lease_store,
        dispatch_reader=dispatch_store,
        execution_base_resolver=execution_base_resolver,
    )
    controller = _UnusedController()
    coordinator = OperatorRecoveryCoordinator(
        planner=planner,
        audit_store=evidence_store,
        run_controller=controller,
        run_reconciler=run_reconciler,
    )
    try:
        run_id = await _start_run(evidence_store, dag_store)
        initial = await coordinator.get_plan(run_id)
        assert len(initial.actions) == 1
        action_id = initial.actions[0].action_id

        first, second = await asyncio.gather(
            coordinator.execute(run_id=run_id, action_id=action_id),
            coordinator.execute(run_id=run_id, action_id=action_id),
        )

        assert first.request_evidence_id == second.request_evidence_id
        assert actor.calls == 1
        assert controller.calls == 0
        assert set(actor.payloads[0]) == {"dispatch_id", "run_id", "task_id"}

        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id="A")
        assert len(attempts) == 1
        assert attempts[0].state.value == "ENQUEUED"

        snapshot = await evidence_store.load_run(run_id)
        operator_evidence = [
            item
            for item in snapshot.evidence
            if item.kind is PersistenceEvidenceKind.OPERATOR_ACTION
        ]
        assert len(operator_evidence) == 1
        assert operator_evidence[0].payload["action_id"] == action_id
        assert operator_evidence[0].payload["fresh_revalidation_required"] is True

        refreshed = await coordinator.get_plan(run_id)
        assert len(refreshed.actions) == 1
        assert refreshed.actions[0].action_id != action_id

        with pytest.raises(OperatorActionStaleError):
            await coordinator.execute(run_id=run_id, action_id=action_id)
        assert actor.calls == 1
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()
