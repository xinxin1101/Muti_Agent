from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dramatiq.errors import BrokerConnectionError
from sqlalchemy import select

from app.dispatch import TaskDispatchBrokerError
from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskReconciliationAction,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
    StaleRunTokenError,
    create_postgres_engine,
    create_session_factory,
)
from app.persistence.fencing import database_time
from app.persistence.models import TaskRow
from app.runtime import IdempotentTaskReconciler


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for reconciliation tests")
    pytest.skip("PostgreSQL reconciliation tests require DEVFLOW_DATABASE_URL")


def _task(task_id: str = "RECONCILE-1") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Prove durable idempotent task reconciliation.",
        readable_files=["src/**"],
        writable_files=["src/reconcile.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Recovery dispatch remains fenced and idempotent."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


async def _new_run(store: PostgresEvidenceStore, task: TaskContract) -> UUID:
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/reconcile.git",
        default_branch="main",
    )
    return await store.start_run(
        project_id=project_id,
        tasks=(task,),
        base_commit="a" * 40,
    )


async def _expire_current_generation(database_url: str, run_id: UUID, task_id: str) -> None:
    engine = create_postgres_engine(database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory.begin() as session:
            task = (
                await session.execute(
                    select(TaskRow)
                    .where(TaskRow.run_id == run_id, TaskRow.task_id == task_id)
                    .with_for_update()
                )
            ).scalar_one()
            observed_at = await database_time(session)
            task.lease_until = observed_at - timedelta(seconds=1)
    finally:
        await engine.dispose()


def _success_execution(
    *,
    run_id: UUID,
    task_id: str,
    dispatch_id: UUID,
) -> WorkerExecutionEvidence:
    result = SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )
    return WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit="a" * 40,
        branch_name="devflow/reconciliation-terminal",
        commit_sha="b" * 40,
        run_result=result,
        duration_ms=25,
    )


class _AckActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def send(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        return SimpleNamespace(message_id=f"reconcile-message-{self.calls}")


class _UnavailableActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, _payload):
        self.calls += 1
        raise BrokerConnectionError("simulated reconciliation broker failure")


def test_unowned_reconciliation_is_idempotent_under_concurrency() -> None:
    asyncio.run(_unowned_reconciliation_is_idempotent_under_concurrency())


async def _unowned_reconciliation_is_idempotent_under_concurrency() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("UNOWNED-RACE")
        run_id = await _new_run(evidence_store, task)

        outcomes = await asyncio.gather(
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
        )

        assert actor.calls == 1
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
        assert attempts[0].attempt_number == 1
        assert attempts[0].state.value == "ENQUEUED"
        assert sum(outcome.receipt is not None for outcome in outcomes) == 1
        waiting = [outcome for outcome in outcomes if outcome.receipt is None]
        assert len(waiting) == 1
        assert waiting[0].decision.action is TaskReconciliationAction.WAIT_EXISTING_DISPATCH
    finally:
        await dispatch_store.dispose()
        await reconciler.dispose()
        await evidence_store.dispose()


def test_expired_generation_recovers_once_and_fences_old_token() -> None:
    asyncio.run(_expired_generation_recovers_once_and_fences_old_token())


async def _expired_generation_recovers_once_and_fences_old_token() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("EXPIRED-TAKEOVER")
        run_id = await _new_run(evidence_store, task)
        first_dispatch = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=first_dispatch,
            run_id=run_id,
            task_id=task.task_id,
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=first_dispatch,
            run_id=run_id,
            task_id=task.task_id,
            broker_message_id="initial-message",
            queue_name="devflow_tasks",
        )
        first_grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-generation-1",
            dispatch_id=first_dispatch,
            lease_seconds=60,
        )
        assert first_grant.snapshot.generation == 1
        await _expire_current_generation(database_url, run_id, task.task_id)

        outcomes = await asyncio.gather(
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
        )

        assert actor.calls == 1
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert [attempt.attempt_number for attempt in attempts] == [1, 2]
        assert attempts[-1].state.value == "ENQUEUED"
        assert attempts[-1].dispatch_id != first_dispatch
        assert sum(outcome.receipt is not None for outcome in outcomes) == 1

        recovery_dispatch = attempts[-1].dispatch_id
        second_grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-generation-2",
            dispatch_id=recovery_dispatch,
            lease_seconds=60,
        )
        assert second_grant.snapshot.generation == 2
        assert second_grant.run_token != first_grant.run_token

        with pytest.raises(StaleRunTokenError):
            async with lease_store.guard_task_git_mutation(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=first_dispatch,
                run_token=first_grant.run_token,
            ):
                pass
    finally:
        await reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_active_generation_never_redispatches() -> None:
    asyncio.run(_active_generation_never_redispatches())


async def _active_generation_never_redispatches() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("ACTIVE-OWNER")
        run_id = await _new_run(evidence_store, task)
        dispatch_id = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            broker_message_id="active-message",
            queue_name="devflow_tasks",
        )
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="live-worker",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )

        outcome = await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert outcome.receipt is None
        assert outcome.decision.action is TaskReconciliationAction.WAIT_ACTIVE_OWNER
        assert actor.calls == 0
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
    finally:
        await reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_terminal_worker_evidence_is_resumed_without_rerun() -> None:
    asyncio.run(_terminal_worker_evidence_is_resumed_without_rerun())


async def _terminal_worker_evidence_is_resumed_without_rerun() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("TERMINAL-RESUME")
        run_id = await _new_run(evidence_store, task)
        dispatch_id = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            broker_message_id="terminal-message",
            queue_name="devflow_tasks",
        )
        grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="terminal-worker",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )
        execution = _success_execution(
            run_id=run_id,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
        )
        evidence_id = await evidence_store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key=f"reconciliation:{dispatch_id}:execution",
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            payload_model=execution,
            stage="worker",
            run_token=grant.run_token,
        )

        outcome = await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert outcome.receipt is None
        assert outcome.decision.action is TaskReconciliationAction.RESUME_TERMINAL_EVIDENCE
        assert outcome.decision.terminal_worker_evidence_id == evidence_id
        assert actor.calls == 0
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
    finally:
        await reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_publish_failed_attempt_is_not_implicitly_republished() -> None:
    asyncio.run(_publish_failed_attempt_is_not_implicitly_republished())


async def _publish_failed_attempt_is_not_implicitly_republished() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _UnavailableActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("PUBLISH-FAILED")
        run_id = await _new_run(evidence_store, task)

        with pytest.raises(TaskDispatchBrokerError, match="broker could not accept"):
            await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert actor.calls == 1
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
        assert attempts[0].state.value == "PUBLISH_FAILED"

        outcome = await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert outcome.receipt is None
        assert outcome.decision.action is TaskReconciliationAction.BLOCKED_PUBLISH_FAILED
        assert actor.calls == 1
        repeated = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert repeated == attempts
    finally:
        await reconciler.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()
