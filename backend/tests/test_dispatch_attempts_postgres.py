from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.dispatch import DurableDramatiqTaskDispatcher
from app.models.dispatch_attempt import DispatchAttemptState
from app.models.task import TaskContract
from app.persistence import PostgresDispatchAttemptStore, PostgresEvidenceStore
from app.persistence.errors import PersistenceConflictError


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for dispatch ledger tests")
    pytest.skip("PostgreSQL dispatch ledger tests require DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Exercise durable dispatch publication state.",
        readable_files=["src/**"],
        writable_files=[f"src/{task_id.lower()}.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Dispatch publication remains evidence-bound."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


async def _new_run(
    evidence_store: PostgresEvidenceStore,
    *tasks: TaskContract,
) -> UUID:
    project_id = await evidence_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/dispatch-ledger.git",
        default_branch="main",
    )
    return await evidence_store.start_run(
        project_id=project_id,
        tasks=tasks,
        base_commit="a" * 40,
    )


def test_dispatch_attempt_store_enforces_monotonic_publication_state() -> None:
    asyncio.run(_dispatch_attempt_store_enforces_monotonic_publication_state())


async def _dispatch_attempt_store_enforces_monotonic_publication_state() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    try:
        task_a = _task("DISPATCH-A")
        task_b = _task("DISPATCH-B")
        run_id = await _new_run(evidence_store, task_a, task_b)

        dispatch_a = uuid4()
        requested, created = await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_a,
            run_id=run_id,
            task_id=task_a.task_id,
        )
        assert created is True
        assert requested.state is DispatchAttemptState.REQUESTED
        assert requested.attempt_number == 1
        assert requested.resolved_at is None

        replay, created = await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_a,
            run_id=run_id,
            task_id=task_a.task_id,
        )
        assert created is False
        assert replay == requested

        with pytest.raises(PersistenceConflictError, match="already has"):
            await dispatch_store.begin_initial_attempt(
                dispatch_id=uuid4(),
                run_id=run_id,
                task_id=task_a.task_id,
            )

        enqueued = await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_a,
            run_id=run_id,
            task_id=task_a.task_id,
            broker_message_id="broker-message-a",
            queue_name="devflow_tasks",
        )
        assert enqueued.state is DispatchAttemptState.ENQUEUED
        assert enqueued.broker_message_id == "broker-message-a"
        assert enqueued.queue_name == "devflow_tasks"
        assert enqueued.resolved_at is not None

        idempotent = await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_a,
            run_id=run_id,
            task_id=task_a.task_id,
            broker_message_id="broker-message-a",
            queue_name="devflow_tasks",
        )
        assert idempotent == enqueued

        with pytest.raises(PersistenceConflictError, match="cannot be replaced"):
            await dispatch_store.mark_enqueued(
                dispatch_id=dispatch_a,
                run_id=run_id,
                task_id=task_a.task_id,
                broker_message_id="different-message",
                queue_name="devflow_tasks",
            )
        with pytest.raises(PersistenceConflictError, match="cannot be rewritten"):
            await dispatch_store.mark_publish_failed(
                dispatch_id=dispatch_a,
                run_id=run_id,
                task_id=task_a.task_id,
                error_code="BROKER_CONNECTION_ERROR",
                error_message="broker unavailable",
            )

        dispatch_b = uuid4()
        _, created = await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_b,
            run_id=run_id,
            task_id=task_b.task_id,
        )
        assert created is True
        failed = await dispatch_store.mark_publish_failed(
            dispatch_id=dispatch_b,
            run_id=run_id,
            task_id=task_b.task_id,
            error_code="BROKER_CONNECTION_ERROR",
            error_message="broker unavailable",
        )
        assert failed.state is DispatchAttemptState.PUBLISH_FAILED
        assert failed.error_code == "BROKER_CONNECTION_ERROR"
        assert failed.resolved_at is not None

        same_failed = await dispatch_store.mark_publish_failed(
            dispatch_id=dispatch_b,
            run_id=run_id,
            task_id=task_b.task_id,
            error_code="BROKER_CONNECTION_ERROR",
            error_message="broker unavailable",
        )
        assert same_failed == failed

        with pytest.raises(PersistenceConflictError, match="different persisted Run/Task"):
            await dispatch_store.begin_initial_attempt(
                dispatch_id=dispatch_a,
                run_id=run_id,
                task_id=task_b.task_id,
            )
        with pytest.raises(PersistenceConflictError, match="cannot be rewritten"):
            await dispatch_store.mark_enqueued(
                dispatch_id=dispatch_b,
                run_id=run_id,
                task_id=task_b.task_id,
                broker_message_id="late-message",
                queue_name="devflow_tasks",
            )
    finally:
        await dispatch_store.dispose()
        await evidence_store.dispose()


class _CrashAfterRequestActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, _payload):
        self.calls += 1
        raise RuntimeError("simulated process failure around broker publication")


class _AckActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, _payload):
        self.calls += 1
        return SimpleNamespace(message_id="acknowledged-message")


def test_durable_dispatcher_preserves_requested_crash_window_and_enqueued_replay() -> None:
    asyncio.run(_durable_dispatcher_preserves_crash_window_and_enqueued_replay())


async def _durable_dispatcher_preserves_crash_window_and_enqueued_replay() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    try:
        crash_task = _task("CRASH-WINDOW")
        crash_run = await _new_run(evidence_store, crash_task)
        crash_actor = _CrashAfterRequestActor()
        crash_dispatcher = DurableDramatiqTaskDispatcher(
            run_store=evidence_store,
            ledger=dispatch_store,
            actor=crash_actor,
        )
        crash_dispatch_id = uuid4()
        with pytest.raises(RuntimeError, match="simulated process failure"):
            await crash_dispatcher.dispatch(
                run_id=crash_run,
                task_id=crash_task.task_id,
                dispatch_id=crash_dispatch_id,
            )
        persisted = await dispatch_store.load(crash_dispatch_id)
        assert persisted is not None
        assert persisted.state is DispatchAttemptState.REQUESTED
        assert persisted.broker_message_id is None
        assert persisted.error_code is None
        assert crash_actor.calls == 1

        queued_task = _task("ENQUEUED-REPLAY")
        queued_run = await _new_run(evidence_store, queued_task)
        ack_actor = _AckActor()
        queued_dispatcher = DurableDramatiqTaskDispatcher(
            run_store=evidence_store,
            ledger=dispatch_store,
            actor=ack_actor,
        )
        queued_dispatch_id = uuid4()
        receipt = await queued_dispatcher.dispatch(
            run_id=queued_run,
            task_id=queued_task.task_id,
            dispatch_id=queued_dispatch_id,
        )
        assert receipt.broker_message_id == "acknowledged-message"
        assert ack_actor.calls == 1

        replay = await queued_dispatcher.dispatch(
            run_id=queued_run,
            task_id=queued_task.task_id,
            dispatch_id=queued_dispatch_id,
        )
        assert replay == receipt
        assert ack_actor.calls == 1
    finally:
        await dispatch_store.dispose()
        await evidence_store.dispose()
