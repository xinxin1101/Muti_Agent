from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from app.models.agent import AgentResponse, AgentRole, TokenUsage
from app.models.events import RuntimeEventKind, RuntimeEventSource
from app.models.task import TaskContract
from app.persistence import PostgresTaskLeaseStore
from app.persistence.types import PersistenceEvidenceKind
from app.trace.collector import TaskTraceCollector
from app.trace.persistence import TraceAwarePostgresEvidenceStore


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for trace persistence tests")
    pytest.skip("PostgreSQL trace test requires DEVFLOW_DATABASE_URL")


def test_trace_batch_round_trips_through_fenced_postgres_evidence() -> None:
    asyncio.run(_trace_batch_round_trip())


async def _trace_batch_round_trip() -> None:
    database_url = _database_url()
    task = TaskContract(
        task_id="TRACE-PG",
        objective="Persist diagnostic trace metadata.",
        writable_files=["trace.py"],
        acceptance_criteria=["trace persists"],
        verification_commands=["pytest -q"],
    )
    dispatch_id = uuid4()
    store = TraceAwarePostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    try:
        project_id = await store.ensure_project(
            repository_url=f"https://example.test/{uuid4()}/trace.git",
            default_branch="main",
        )
        run_id = await store.start_run(
            project_id=project_id,
            tasks=[task],
            base_commit="a" * 40,
        )
        grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="trace-test-worker",
            dispatch_id=dispatch_id,
            lease_seconds=30,
        )
        collector = TaskTraceCollector(
            run_id=run_id,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
            generation=grant.snapshot.generation,
        )
        collector.record_agent_turn(
            role=AgentRole.DEVELOPER,
            iteration=1,
            response=AgentResponse(
                model="trace-model",
                content="this completion must not be stored in TRACE_BATCH",
                tool_calls=[],
                usage=TokenUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                latency_ms=4,
                finish_reason="stop",
            ),
        )

        evidence_id = await store.append_evidence(
            run_id=run_id,
            task_id=task.task_id,
            evidence_key=f"dispatch:{dispatch_id}:trace",
            kind=PersistenceEvidenceKind.TRACE_BATCH,
            payload_model=collector.batch(),
            stage="trace",
            run_token=grant.run_token,
        )

        snapshot = await store.load_run(run_id)
        trace_evidence = next(item for item in snapshot.evidence if item.id == evidence_id)
        assert trace_evidence.kind is PersistenceEvidenceKind.TRACE_BATCH
        serialized = str(trace_evidence.payload)
        assert "trace-model" in serialized
        assert "this completion must not be stored" not in serialized

        events = await store.list_runtime_events(run_id, limit=100)
        trace_event = next(
            event
            for event in events
            if event.kind is RuntimeEventKind.EVIDENCE_RECORDED
            and event.attributes.get("evidence_id") == evidence_id
        )
        assert trace_event.source is RuntimeEventSource.AGENT
        assert trace_event.dispatch_id == dispatch_id
        assert trace_event.generation == grant.snapshot.generation
        assert trace_event.attributes["diagnostic_only"] is True
        assert trace_event.attributes["privacy_mode"] == "METADATA_ONLY"
    finally:
        await lease_store.dispose()
        await store.dispose()
