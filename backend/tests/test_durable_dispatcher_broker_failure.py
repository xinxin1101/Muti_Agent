from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from dramatiq.errors import BrokerConnectionError

from app.dispatch import (
    DurableDramatiqTaskDispatcher,
    TaskDispatchBrokerError,
    TaskDispatchRejectedError,
)
from app.models.dispatch_attempt import DispatchAttemptState
from app.models.task import TaskContract
from app.persistence import PostgresDispatchAttemptStore, PostgresEvidenceStore


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for durable dispatch tests")
    pytest.skip("PostgreSQL durable dispatch tests require DEVFLOW_DATABASE_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="BROKER-FAILURE",
        objective="Preserve a bounded failed broker publication observation.",
        readable_files=["src/**"],
        writable_files=["src/broker_failure.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Failed publication is durable but never treated as non-delivery proof."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


class _UnavailableActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, _payload):
        self.calls += 1
        raise BrokerConnectionError("simulated broker connection failure")


def test_broker_error_becomes_durable_failure_without_automatic_republish() -> None:
    asyncio.run(_broker_error_becomes_durable_failure_without_automatic_republish())


async def _broker_error_becomes_durable_failure_without_automatic_republish() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    try:
        project_id = await evidence_store.ensure_project(
            repository_url=f"https://example.test/{uuid4()}/broker-failure.git",
            default_branch="main",
        )
        task = _task()
        run_id = await evidence_store.start_run(
            project_id=project_id,
            tasks=(task,),
            base_commit="a" * 40,
        )
        actor = _UnavailableActor()
        dispatcher = DurableDramatiqTaskDispatcher(
            run_store=evidence_store,
            ledger=dispatch_store,
            actor=actor,
        )
        dispatch_id = uuid4()

        with pytest.raises(TaskDispatchBrokerError, match="broker could not accept"):
            await dispatcher.dispatch(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
            )

        persisted = await dispatch_store.load(dispatch_id)
        assert persisted is not None
        assert persisted.state is DispatchAttemptState.PUBLISH_FAILED
        assert persisted.error_code == "BROKER_CONNECTION_ERROR"
        assert persisted.error_message == "Dramatiq broker could not accept task dispatch"
        assert actor.calls == 1

        with pytest.raises(TaskDispatchRejectedError, match="recovery must decide"):
            await dispatcher.dispatch(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
            )
        assert actor.calls == 1
    finally:
        await dispatch_store.dispose()
        await evidence_store.dispose()
