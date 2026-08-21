from __future__ import annotations

import asyncio
from contextvars import ContextVar
from time import perf_counter
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.models.dispatch import (
    TaskDispatchEnvelope,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.persistence.types import PersistenceEvidenceKind
from app.trace.collector import TaskTraceCollector
from app.workers.executor import (
    LocalQueuedTaskExecutionBackend,
    QueuedTaskWorker,
)
from app.workspace import TaskWorktreeManager, TaskWorktreeRecord

_TRACE_GENERATION: ContextVar[int | None] = ContextVar(
    "devflow_trace_generation",
    default=None,
)


class TraceEvidenceSink(Protocol):
    async def append_evidence(
        self,
        *,
        run_id: UUID,
        evidence_key: str,
        kind: PersistenceEvidenceKind,
        payload_model: BaseModel,
        task_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
        run_token: UUID | None = None,
    ) -> int: ...


class TraceAwareQueuedTaskWorker:
    """Add generation correlation around an already-accepted QueuedTaskWorker.

    Generation is diagnostic correlation metadata here. The fenced `run_token` still reaches the
    underlying worker through the original execution API and remains the only write capability.
    """

    def __init__(self, worker: QueuedTaskWorker) -> None:
        self._worker = worker

    async def execute(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
    ) -> WorkerExecutionEvidence:
        return await self._worker.execute(envelope, run_token=run_token)

    async def execute_generation(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
        generation: int,
    ) -> WorkerExecutionEvidence:
        if generation < 1:
            raise ValueError("trace generation must be positive")
        context_token = _TRACE_GENERATION.set(generation)
        try:
            return await self._worker.execute(envelope, run_token=run_token)
        finally:
            _TRACE_GENERATION.reset(context_token)


class TraceAwareLocalQueuedTaskExecutionBackend(LocalQueuedTaskExecutionBackend):
    """Collect and best-effort persist metadata-only trace spans for production workers.

    TRACE_BATCH persistence is intentionally non-authoritative. A trace write failure cannot turn
    successful execution into failure or authorize a retry. The ordinary worker evidence writes,
    lease fencing, Git mutation guard, verification and completion paths remain unchanged.
    """

    def __init__(
        self,
        *,
        trace_store: TraceEvidenceSink,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._trace_store = trace_store

    async def execute(
        self,
        *,
        task: TaskContract,
        project_id: UUID,
        run_id: UUID,
        dispatch_id: UUID,
        run_token: UUID,
        base_commit: str,
    ) -> WorkerExecutionEvidence:
        generation = _TRACE_GENERATION.get()
        if generation is None:
            return await super().execute(
                task=task,
                project_id=project_id,
                run_id=run_id,
                dispatch_id=dispatch_id,
                run_token=run_token,
                base_commit=base_commit,
            )

        trace = TaskTraceCollector(
            run_id=run_id,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
            generation=generation,
        )
        started_at = perf_counter()
        record: TaskWorktreeRecord | None = None
        run_result: SingleTaskRunResult | None = None
        trace_persisted = False
        worktree_identity = self._worktree_identity(run_id, task.task_id, run_token)
        try:
            base_workspace = self._workspace_resolver.resolve(project_id)
            worktrees = TaskWorktreeManager(
                base_workspace,
                self._worktree_root / str(run_id),
                frozen_base_commit=base_commit,
            )

            async with self._git_fence.guard_task_git_mutation(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                run_token=run_token,
            ):
                record = await asyncio.to_thread(worktrees.create, worktree_identity)
            workspace = worktrees.open_workspace(worktree_identity)

            runner = self._runner_factory(task)
            run_result = await runner.run(
                task,
                workspace=workspace,
                trace=trace,
            )
            await self._persist_trace(trace, run_token=run_token)
            trace_persisted = True

            if run_result.status is TaskRunState.FAILED:
                return self._failure_evidence(
                    task=task,
                    run_id=run_id,
                    dispatch_id=dispatch_id,
                    base_commit=base_commit,
                    started_at=started_at,
                    record=record,
                    run_result=run_result,
                    failures=tuple(run_result.failures),
                )
            if run_result.status is not TaskRunState.SUCCEEDED:
                raise RuntimeError("single-task runtime returned a non-terminal status")

            async with self._git_fence.guard_task_git_mutation(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                run_token=run_token,
            ):
                commit_sha = await asyncio.to_thread(
                    worktrees.commit_task_changes,
                    worktree_identity,
                )
            return WorkerExecutionEvidence(
                dispatch_id=dispatch_id,
                run_id=run_id,
                task_id=task.task_id,
                status=WorkerExecutionStatus.SUCCEEDED,
                base_commit=base_commit,
                branch_name=record.branch_name,
                commit_sha=commit_sha,
                run_result=run_result,
                failures=(),
                duration_ms=self._duration_ms(started_at),
            )
        except Exception as exc:
            if not trace_persisted:
                await self._persist_trace(trace, run_token=run_token)
            failure = self._runtime_failure(
                "Queued worker execution failed before fenced task-branch finalization.",
                evidence=[f"exception_type={type(exc).__name__}"],
            )
            return self._failure_evidence(
                task=task,
                run_id=run_id,
                dispatch_id=dispatch_id,
                base_commit=base_commit,
                started_at=started_at,
                record=record,
                run_result=run_result,
                failures=(failure,),
            )

    async def _persist_trace(
        self,
        trace: TaskTraceCollector,
        *,
        run_token: UUID,
    ) -> None:
        if trace.empty:
            return
        batch = trace.batch()
        try:
            await self._trace_store.append_evidence(
                run_id=batch.run_id,
                task_id=batch.task_id,
                evidence_key=f"dispatch:{batch.dispatch_id}:trace",
                kind=PersistenceEvidenceKind.TRACE_BATCH,
                payload_model=batch,
                stage="trace",
                run_token=run_token,
            )
        except Exception:
            # Trace liveness is intentionally weaker than execution truth. The existing fenced
            # worker/evidence path still decides whether this generation may publish real results.
            return
