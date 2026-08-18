from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from dramatiq import Worker

from app.dispatch import DramatiqTaskDispatcher, create_redis_broker
from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDispatchEnvelope,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import PersistenceEvidenceKind, PostgresEvidenceStore
from app.persistence.types import PersistedRunStatus
from app.workers.actor import create_task_actor
from app.workers.executor import QueuedTaskWorker


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for queue integration tests")
    pytest.skip("queue integration test requires DEVFLOW_DATABASE_URL")


def _redis_url() -> str:
    value = os.environ.get("DEVFLOW_REDIS_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_REDIS_URL for queue integration tests")
    pytest.skip("queue integration test requires DEVFLOW_REDIS_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="REDIS-WORKER",
        objective="Execute one persisted task through a Redis-backed worker.",
        readable_files=["src/**"],
        writable_files=["src/service.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["The task produces validated runtime evidence."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _success_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Verified."),
        ],
        changed_files=["src/service.py"],
    )


class _FakeExecutionBackend:
    def __init__(self, result: SingleTaskRunResult) -> None:
        self._result = result

    async def execute(
        self,
        *,
        task: TaskContract,
        project_id,
        run_id,
        dispatch_id,
        base_commit: str,
    ) -> WorkerExecutionEvidence:
        assert task.task_id == self._result.task_id
        assert project_id
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.SUCCEEDED,
            base_commit=base_commit,
            branch_name="devflow/task/redis-worker",
            commit_sha="b" * 40,
            run_result=self._result,
            duration_ms=1,
        )


def test_real_redis_worker_persists_typed_postgresql_evidence() -> None:
    asyncio.run(_real_redis_worker_round_trip())


async def _real_redis_worker_round_trip() -> None:
    database_url = _database_url()
    redis_url = _redis_url()
    task = _task()
    result = _success_result(task.task_id)

    store = PostgresEvidenceStore.from_url(database_url)
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/repo.git",
        default_branch="main",
    )
    run_id = await store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit="a" * 40,
    )

    namespace = f"devflow-test-{uuid4().hex}"
    queue_name = f"devflow_tasks_{uuid4().hex}"
    broker = create_redis_broker(redis_url, namespace=namespace)

    async def handler(envelope: TaskDispatchEnvelope) -> WorkerExecutionEvidence:
        worker_store = PostgresEvidenceStore.from_url(database_url)
        try:
            worker = QueuedTaskWorker(
                store=worker_store,
                backend=_FakeExecutionBackend(result),
            )
            return await worker.execute(envelope)
        finally:
            await worker_store.dispose()

    actor = create_task_actor(
        broker=broker,
        handler=handler,
        queue_name=queue_name,
    )
    dramatiq_worker = Worker(
        broker,
        queues={queue_name},
        worker_timeout=50,
        worker_threads=1,
    )
    dramatiq_worker.start()

    try:
        dispatcher = DramatiqTaskDispatcher(store=store, actor=actor)
        receipt = await dispatcher.dispatch(run_id=run_id, task_id=task.task_id)

        deadline = asyncio.get_running_loop().time() + 8.0
        while True:
            snapshot = await store.load_run(run_id)
            if snapshot.status is PersistedRunStatus.SUCCEEDED:
                break
            if asyncio.get_running_loop().time() >= deadline:
                pytest.fail("Redis/Dramatiq worker did not persist terminal evidence in time")
            await asyncio.sleep(0.05)

        kinds = [item.kind for item in snapshot.evidence]
        assert receipt.run_id == run_id
        assert kinds.count(PersistenceEvidenceKind.DISPATCH_EVENT) == 2
        assert PersistenceEvidenceKind.WORKER_EXECUTION in kinds
        assert PersistenceEvidenceKind.STATE_TRANSITION in kinds
        worker_evidence = await store.list_evidence(
            run_id,
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
        )
        assert len(worker_evidence) == 1
        assert worker_evidence[0].payload["dispatch_id"] == str(receipt.dispatch_id)
        assert snapshot.terminal_result is not None
    finally:
        dramatiq_worker.stop(timeout=5_000)
        broker.flush_all()
        broker.close()
        await store.dispose()
