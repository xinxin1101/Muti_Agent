from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.checkpoint import TaskCheckpoint
from app.models.continuation import TaskContinuationSummary
from app.models.failure import FailureReport
from app.models.run import SingleTaskRunResult, TaskRunState


class WorkerExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class WorkerDispatchPhase(StrEnum):
    RECEIVED = "RECEIVED"
    COMPLETED = "COMPLETED"


class TaskDispatchEnvelope(BaseModel):
    """Minimal queue payload. Repository content and credentials never enter Redis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)


class TaskDispatchReceipt(BaseModel):
    """Broker acknowledgement returned after Dramatiq accepts an enqueue call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    broker_message_id: str = Field(min_length=1, max_length=128)
    queue_name: str = Field(min_length=1, max_length=128)


class WorkerDispatchEvent(BaseModel):
    """Append-only worker-side evidence around one broker dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    phase: WorkerDispatchPhase
    outcome: WorkerExecutionStatus | None = None

    @model_validator(mode="after")
    def validate_phase_shape(self) -> WorkerDispatchEvent:
        if self.phase is WorkerDispatchPhase.RECEIVED and self.outcome is not None:
            raise ValueError("RECEIVED dispatch events must not claim an execution outcome")
        if self.phase is WorkerDispatchPhase.COMPLETED and self.outcome is None:
            raise ValueError("COMPLETED dispatch events require an execution outcome")
        return self


class WorkerExecutionEvidence(BaseModel):
    """Terminal evidence produced by one queued task execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    status: WorkerExecutionStatus
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    branch_name: str | None = Field(default=None, min_length=1, max_length=255)
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    checkpoint: TaskCheckpoint | None = None
    continuation: TaskContinuationSummary | None = None
    run_result: SingleTaskRunResult | None = None
    failures: tuple[FailureReport, ...] = Field(default_factory=tuple)
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> WorkerExecutionEvidence:
        if self.run_result is not None and self.run_result.task_id != self.task_id:
            raise ValueError("worker execution evidence must match the nested task run id")

        if self.status is WorkerExecutionStatus.SUCCEEDED:
            if self.run_result is None or self.run_result.status is not TaskRunState.SUCCEEDED:
                raise ValueError("successful worker execution requires successful runtime evidence")
            if self.failures:
                raise ValueError("successful worker execution must not contain failures")
            if self.commit_sha is None:
                raise ValueError("successful worker execution requires a task commit")
            if self.checkpoint is not None:
                raise ValueError(
                    "successful worker execution must not contain a continuation checkpoint"
                )
            return self

        if self.commit_sha is not None:
            raise ValueError("failed worker execution must not publish a successful task commit")
        if self.checkpoint is not None and self.checkpoint.task_id != self.task_id:
            raise ValueError("worker checkpoint must match the failed task")
        if not self.failures and (
            self.run_result is None or self.run_result.status is not TaskRunState.FAILED
        ):
            raise ValueError("failed worker execution requires explicit failure evidence")
        return self
