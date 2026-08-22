from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from app.models.agent import AgentResponse, AgentRole, TokenUsage
from app.models.dispatch import TaskDispatchEnvelope
from app.trace.collector import TaskTraceCollector
from app.trace.worker import (
    _TRACE_GENERATION,
    TraceAwareLocalQueuedTaskExecutionBackend,
    TraceAwareQueuedTaskWorker,
)


class _FailingTraceStore:
    def __init__(self) -> None:
        self.calls = 0

    async def append_evidence(self, **_kwargs) -> int:
        self.calls += 1
        raise RuntimeError("diagnostic persistence unavailable")


class _GenerationObservingWorker:
    def __init__(self) -> None:
        self.observed: list[int | None] = []

    async def execute(self, envelope, *, run_token):
        del envelope, run_token
        self.observed.append(_TRACE_GENERATION.get())
        return "accepted-runtime-result"


def test_trace_persistence_failure_does_not_escape_sidecar_boundary(tmp_path: Path) -> None:
    asyncio.run(_trace_persistence_failure_does_not_escape_sidecar_boundary(tmp_path))


async def _trace_persistence_failure_does_not_escape_sidecar_boundary(
    tmp_path: Path,
) -> None:
    trace_store = _FailingTraceStore()
    backend = TraceAwareLocalQueuedTaskExecutionBackend(
        trace_store=trace_store,
        workspace_resolver=object(),
        worktree_root=tmp_path / "worktrees",
        runner_factory=lambda _task: object(),
        git_fence=object(),
    )
    collector = TaskTraceCollector(
        run_id=uuid4(),
        task_id="task-a",
        dispatch_id=uuid4(),
        generation=1,
    )
    collector.record_agent_turn(
        role=AgentRole.DEVELOPER,
        iteration=1,
        response=AgentResponse(
            model="model-v1",
            content="not persisted",
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            latency_ms=1,
            finish_reason="stop",
        ),
    )

    await backend._persist_trace(collector, run_token=uuid4())

    assert trace_store.calls == 1


def test_generation_extension_is_diagnostic_and_context_is_reset() -> None:
    asyncio.run(_generation_extension_is_diagnostic_and_context_is_reset())


async def _generation_extension_is_diagnostic_and_context_is_reset() -> None:
    inner = _GenerationObservingWorker()
    worker = TraceAwareQueuedTaskWorker(inner)  # type: ignore[arg-type]
    envelope = TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=uuid4(),
        task_id="task-a",
    )

    result = await worker.execute_generation(
        envelope,
        run_token=uuid4(),
        generation=7,
    )

    assert result == "accepted-runtime-result"
    assert inner.observed == [7]
    assert _TRACE_GENERATION.get() is None
