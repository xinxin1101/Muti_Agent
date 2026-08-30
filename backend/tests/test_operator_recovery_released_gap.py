from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.models import TaskContract, TaskDAG, TaskNode
from app.models.run_reconciliation import DAGTaskFrontierState
from app.persistence import (
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
)
from app.runtime import EvidenceBoundTaskExecutionBaseResolver
from app.runtime.operator_recovery import OperatorRecoveryPlanner

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
        objective="Prove released ownership gaps remain blocked.",
        readable_files=["src/**"],
        writable_files=["src/a.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Released generations cannot be silently reacquired."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def test_released_generation_without_terminal_evidence_advertises_no_operator_mutation() -> None:
    asyncio.run(_released_generation_without_terminal_evidence_advertises_no_operator_mutation())


async def _released_generation_without_terminal_evidence_advertises_no_operator_mutation() -> None:
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
        project_id = await evidence_store.ensure_project(
            repository_url=f"https://example.test/{uuid4()}/released-gap.git",
            default_branch="main",
        )
        run_id = await dag_store.start_run(
            project_id=project_id,
            dag=TaskDAG(tasks=(TaskNode(task=_task(), depends_on=()),)),
            base_commit=_BASE,
        )
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
            broker_message_id="released-gap",
            queue_name="devflow_tasks",
        )
        grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id="A",
            owner_id="worker-before-gap",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )
        await lease_store.release_task_lease(
            run_id=run_id,
            task_id="A",
            owner_id="worker-before-gap",
            dispatch_id=dispatch_id,
            run_token=grant.run_token,
        )

        plan = await planner.build_plan(run_id)

        assert plan.actions == ()
        assert (
            plan.reconciliation.tasks[0].frontier_state is DAGTaskFrontierState.BLOCKED_RECOVERY_GAP
        )
        assert "released" in plan.reconciliation.tasks[0].reason.lower()
    finally:
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()
