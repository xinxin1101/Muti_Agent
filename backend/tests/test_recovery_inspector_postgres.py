from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.models import RecoveryDisposition, TaskContract, TaskLeaseState
from app.persistence import PostgresEvidenceStore, PostgresTaskLeaseStore
from app.runtime import RecoveryInspector


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for recovery inspector tests")
    pytest.skip("PostgreSQL recovery inspector test requires DEVFLOW_DATABASE_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="RECOVERY-PG",
        objective="Inspect persisted recovery state without mutating runtime authority.",
        readable_files=["src/**"],
        writable_files=["src/recovery.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Recovery inspection is read-only and evidence-bound."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def test_recovery_inspector_reads_postgres_without_mutating_runtime_truth() -> None:
    asyncio.run(_inspect_postgres_without_mutation())


async def _inspect_postgres_without_mutation() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    try:
        project_id = await evidence_store.ensure_project(
            repository_url=f"https://example.test/{uuid4()}/recovery-inspector.git",
            default_branch="main",
        )
        task = _task()
        run_id = await evidence_store.start_run(
            project_id=project_id,
            tasks=(task,),
            base_commit="a" * 40,
        )

        before_run = await evidence_store.load_run(run_id)
        before_events = await evidence_store.list_runtime_events(run_id)
        before_lease = await lease_store.inspect_task_lease(
            run_id=run_id,
            task_id=task.task_id,
        )
        assert before_lease.state is TaskLeaseState.UNOWNED

        inspector = RecoveryInspector(
            run_reader=evidence_store,
            lease_reader=lease_store,
        )
        plan = await inspector.inspect_run(run_id)

        assert plan.run_id == run_id
        assert len(plan.tasks) == 1
        assessment = plan.tasks[0]
        assert assessment.task_id == task.task_id
        assert assessment.lease_state is TaskLeaseState.UNOWNED
        assert (
            assessment.disposition
            is RecoveryDisposition.BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
        )

        after_run = await evidence_store.load_run(run_id)
        after_events = await evidence_store.list_runtime_events(run_id)
        after_lease = await lease_store.inspect_task_lease(
            run_id=run_id,
            task_id=task.task_id,
        )

        assert after_run == before_run
        assert after_events == before_events
        assert after_lease.state == before_lease.state
        assert after_lease.generation == before_lease.generation
        assert after_lease.owner_id == before_lease.owner_id
        assert after_lease.dispatch_id == before_lease.dispatch_id
        assert after_lease.acquired_at == before_lease.acquired_at
        assert after_lease.heartbeat_at == before_lease.heartbeat_at
        assert after_lease.lease_until == before_lease.lease_until
        assert after_lease.released_at == before_lease.released_at
    finally:
        await lease_store.dispose()
        await evidence_store.dispose()
