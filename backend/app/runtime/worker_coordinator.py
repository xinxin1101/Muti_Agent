from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import perf_counter
from typing import Protocol

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.scheduler import TaskScheduleState
from app.models.task import TaskContract
from app.models.worker import ParallelWorkerWaveResult, WorkerTaskResult
from app.runtime.scheduler import DAGScheduler
from app.workspace import LocalGitWorkspace, TaskWorktreeManager, TaskWorktreeRecord


class SingleTaskRunner(Protocol):
    """Minimal async boundary implemented by the accepted V0.1 orchestrator."""

    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> SingleTaskRunResult: ...


class ParallelWorkerCoordinator:
    """Execute one deterministic wave of scheduler-READY tasks with bounded concurrency."""

    def __init__(
        self,
        *,
        scheduler: DAGScheduler,
        worktrees: TaskWorktreeManager,
        runner_factory: Callable[[TaskContract], SingleTaskRunner],
        max_concurrency: int = 2,
        task_base_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self._scheduler = scheduler
        self._worktrees = worktrees
        self._runner_factory = runner_factory
        self._max_concurrency = max_concurrency
        self._task_base_resolver = task_base_resolver
        self._wave_active = False

    async def run_ready_wave(self) -> ParallelWorkerWaveResult:
        """Run only the READY snapshot present when this wave begins.

        Newly-ready downstream tasks are reported in `next_ready_task_ids` but are deliberately
        not started. Step 2.5 must integrate successful task branches before a downstream task
        receives a dependency-aware base commit.
        """

        if self._wave_active:
            raise RuntimeError("parallel worker coordinator already has an active READY wave")
        self._wave_active = True
        try:
            return await self._run_ready_wave_once()
        finally:
            self._wave_active = False

    async def _run_ready_wave_once(self) -> ParallelWorkerWaveResult:
        scheduled = self._scheduler.ready_task_ids()
        if not scheduled:
            return ParallelWorkerWaveResult(
                scheduled_task_ids=(),
                task_results=(),
                next_ready_task_ids=self._scheduler.ready_task_ids(),
                scheduler_snapshot=self._scheduler.snapshot(),
                max_concurrency=self._max_concurrency,
                peak_concurrency=0,
            )

        task_bases = self._resolve_task_bases(scheduled)
        semaphore = asyncio.Semaphore(self._max_concurrency)
        git_lock = asyncio.Lock()
        active_workers = 0
        peak_concurrency = 0

        async def execute(task_id: str) -> WorkerTaskResult:
            nonlocal active_workers, peak_concurrency
            async with semaphore:
                active_workers += 1
                peak_concurrency = max(peak_concurrency, active_workers)
                started_at = perf_counter()
                record: TaskWorktreeRecord | None = None
                run_result: SingleTaskRunResult | None = None
                try:
                    self._scheduler.start(task_id)
                    task = self._scheduler.dag.node(task_id).task

                    async with git_lock:
                        record = await asyncio.to_thread(
                            self._worktrees.create,
                            task_id,
                            base_commit=task_bases[task_id],
                        )
                        workspace = await asyncio.to_thread(
                            self._worktrees.open_workspace,
                            task_id,
                        )

                    runner = self._runner_factory(task)
                    run_result = await runner.run(task, workspace=workspace)

                    if run_result.status is TaskRunState.FAILED:
                        self._scheduler.fail(task_id)
                        return self._result(
                            task_id=task_id,
                            started_at=started_at,
                            record=record,
                            run_result=run_result,
                            failures=tuple(run_result.failures),
                        )

                    if run_result.status is not TaskRunState.SUCCEEDED:
                        raise RuntimeError("single-task runner returned a non-terminal status")

                    async with git_lock:
                        commit_sha = await asyncio.to_thread(
                            self._worktrees.commit_task_changes,
                            task_id,
                        )
                    self._scheduler.succeed(task_id)
                    return self._result(
                        task_id=task_id,
                        started_at=started_at,
                        record=record,
                        run_result=run_result,
                        commit_sha=commit_sha,
                    )
                except asyncio.CancelledError:
                    failure = self._runtime_failure(
                        "Worker task was cancelled before terminal completion.",
                        evidence=["worker_cancelled=true"],
                    )
                    self._fail_if_running(task_id)
                    return self._result(
                        task_id=task_id,
                        started_at=started_at,
                        record=record,
                        run_result=run_result,
                        failures=(failure,),
                    )
                except Exception as exc:
                    failure = self._runtime_failure(
                        "Worker execution failed before successful task-branch finalization.",
                        evidence=[f"exception_type={type(exc).__name__}"],
                    )
                    self._fail_if_running(task_id)
                    return self._result(
                        task_id=task_id,
                        started_at=started_at,
                        record=record,
                        run_result=run_result,
                        failures=(failure,),
                    )
                finally:
                    active_workers -= 1

        tasks = [asyncio.create_task(execute(task_id)) for task_id in scheduled]
        try:
            results = await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        return ParallelWorkerWaveResult(
            scheduled_task_ids=scheduled,
            task_results=tuple(results),
            next_ready_task_ids=self._scheduler.ready_task_ids(),
            scheduler_snapshot=self._scheduler.snapshot(),
            max_concurrency=self._max_concurrency,
            peak_concurrency=peak_concurrency,
        )

    def _resolve_task_bases(self, task_ids: tuple[str, ...]) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        for task_id in task_ids:
            node = self._scheduler.dag.node(task_id)
            requested = self._task_base_resolver(task_id) if self._task_base_resolver else None
            if node.depends_on and requested is None:
                raise RuntimeError(
                    "dependent READY task requires an integrated descendant base commit before "
                    f"worker execution: {task_id}"
                )
            resolved[task_id] = requested
        return resolved

    def _result(
        self,
        *,
        task_id: str,
        started_at: float,
        record: TaskWorktreeRecord | None,
        run_result: SingleTaskRunResult | None,
        failures: tuple[FailureReport, ...] = (),
        commit_sha: str | None = None,
    ) -> WorkerTaskResult:
        return WorkerTaskResult(
            task_id=task_id,
            scheduler_state=self._scheduler.state(task_id),
            worktree_path=str(record.path) if record is not None else None,
            branch_name=record.branch_name if record is not None else None,
            base_commit=record.base_commit if record is not None else None,
            commit_sha=commit_sha,
            run_result=run_result,
            failures=failures,
            duration_ms=max(0, int((perf_counter() - started_at) * 1000)),
        )

    def _fail_if_running(self, task_id: str) -> None:
        if self._scheduler.state(task_id) is TaskScheduleState.RUNNING:
            self._scheduler.fail(task_id)

    @staticmethod
    def _runtime_failure(message: str, *, evidence: list[str]) -> FailureReport:
        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message=message,
            retryable=False,
            evidence=evidence,
        )
