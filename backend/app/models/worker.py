from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.failure import FailureReport
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.scheduler import SchedulerSnapshot, TaskScheduleState


class WorkerTaskResult(BaseModel):
    """Terminal evidence for one scheduler task executed inside a dedicated worktree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    scheduler_state: TaskScheduleState
    worktree_path: str | None = None
    branch_name: str | None = None
    base_commit: str | None = None
    commit_sha: str | None = None
    run_result: SingleTaskRunResult | None = None
    failures: tuple[FailureReport, ...] = Field(default_factory=tuple)
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_terminal_consistency(self) -> WorkerTaskResult:
        if self.scheduler_state not in {
            TaskScheduleState.SUCCEEDED,
            TaskScheduleState.FAILED,
        }:
            raise ValueError("worker task result requires a SUCCEEDED or FAILED scheduler state")

        if self.run_result is not None and self.run_result.task_id != self.task_id:
            raise ValueError("worker task result must match the nested single-task run id")

        if self.scheduler_state is TaskScheduleState.SUCCEEDED:
            if self.run_result is None or self.run_result.status is not TaskRunState.SUCCEEDED:
                raise ValueError("successful worker tasks require successful V0.1 run evidence")
            if self.failures:
                raise ValueError("successful worker tasks must not contain worker failures")
            if not self.commit_sha:
                raise ValueError("successful worker tasks require a committed task-branch SHA")
        elif not self.failures and (
            self.run_result is None or self.run_result.status is not TaskRunState.FAILED
        ):
            raise ValueError("failed worker tasks require explicit failure evidence")

        return self


class ParallelWorkerWaveResult(BaseModel):
    """Evidence bundle for one bounded snapshot of scheduler-READY tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheduled_task_ids: tuple[str, ...]
    task_results: tuple[WorkerTaskResult, ...]
    next_ready_task_ids: tuple[str, ...]
    scheduler_snapshot: SchedulerSnapshot
    max_concurrency: int = Field(ge=1)
    peak_concurrency: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_wave_consistency(self) -> ParallelWorkerWaveResult:
        if len(self.scheduled_task_ids) != len(set(self.scheduled_task_ids)):
            raise ValueError("scheduled_task_ids must be unique")
        if tuple(result.task_id for result in self.task_results) != self.scheduled_task_ids:
            raise ValueError("worker results must preserve the deterministic scheduled task order")
        if self.peak_concurrency > self.max_concurrency:
            raise ValueError("peak worker concurrency must not exceed the configured limit")
        if self.scheduled_task_ids and self.peak_concurrency < 1:
            raise ValueError("non-empty worker waves must observe at least one active worker")
        return self
