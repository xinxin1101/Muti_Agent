from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.dispatch import WorkerExecutionStatus
from app.models.lease import TaskLeaseState


class RecoveryDisposition(StrEnum):
    """Read-only recovery interpretation of one persisted task."""

    NO_ACTION_RUN_TERMINAL = "NO_ACTION_RUN_TERMINAL"
    WAIT_ACTIVE_OWNER = "WAIT_ACTIVE_OWNER"
    RESUME_FROM_TERMINAL_EVIDENCE = "RESUME_FROM_TERMINAL_EVIDENCE"
    REDISPATCH_CANDIDATE_EXPIRED_GENERATION = "REDISPATCH_CANDIDATE_EXPIRED_GENERATION"
    BLOCKED_UNOWNED_DISPATCH_AMBIGUITY = "BLOCKED_UNOWNED_DISPATCH_AMBIGUITY"
    BLOCKED_RELEASED_EVIDENCE_GAP = "BLOCKED_RELEASED_EVIDENCE_GAP"


class TaskRecoveryAssessment(BaseModel):
    """Bounded recovery diagnosis; never a lease or dispatch authorization."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    disposition: RecoveryDisposition
    lease_state: TaskLeaseState
    lease_generation: int = Field(ge=0)
    lease_dispatch_id: UUID | None = None
    observed_at: datetime
    worker_execution_status: WorkerExecutionStatus | None = None
    worker_execution_evidence_id: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_recovery_shape(self) -> TaskRecoveryAssessment:
        worker_values = (
            self.worker_execution_status,
            self.worker_execution_evidence_id,
        )
        if (worker_values[0] is None) != (worker_values[1] is None):
            raise ValueError(
                "worker execution status and evidence id must either both be present "
                "or both be absent"
            )

        if self.lease_state is TaskLeaseState.UNOWNED:
            if self.lease_generation != 0 or self.lease_dispatch_id is not None:
                raise ValueError(
                    "UNOWNED recovery assessments require generation zero and no dispatch"
                )
        else:
            if self.lease_generation < 1 or self.lease_dispatch_id is None:
                raise ValueError(
                    "owned recovery assessments require generation and dispatch identity"
                )

        if self.disposition is RecoveryDisposition.WAIT_ACTIVE_OWNER:
            if self.lease_state is not TaskLeaseState.ACTIVE:
                raise ValueError("WAIT_ACTIVE_OWNER requires an ACTIVE lease")
        elif self.disposition is RecoveryDisposition.RESUME_FROM_TERMINAL_EVIDENCE:
            if self.worker_execution_status is None:
                raise ValueError(
                    "RESUME_FROM_TERMINAL_EVIDENCE requires accepted worker execution evidence"
                )
        elif self.disposition is RecoveryDisposition.REDISPATCH_CANDIDATE_EXPIRED_GENERATION:
            if self.lease_state is not TaskLeaseState.EXPIRED:
                raise ValueError(
                    "REDISPATCH_CANDIDATE_EXPIRED_GENERATION requires an EXPIRED lease"
                )
            if self.worker_execution_status is not None:
                raise ValueError(
                    "redispatch candidates must not already have terminal worker evidence"
                )
        elif self.disposition is RecoveryDisposition.BLOCKED_UNOWNED_DISPATCH_AMBIGUITY:
            if self.lease_state is not TaskLeaseState.UNOWNED:
                raise ValueError("unowned dispatch ambiguity requires an UNOWNED lease")
        elif self.disposition is RecoveryDisposition.BLOCKED_RELEASED_EVIDENCE_GAP:
            if self.lease_state is not TaskLeaseState.RELEASED:
                raise ValueError("released evidence gap requires a RELEASED lease")
            if self.worker_execution_status is not None:
                raise ValueError("released evidence gaps must not claim terminal worker evidence")
        return self


class RunRecoveryPlan(BaseModel):
    """One read-only recovery projection over all persisted tasks in a Run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    run_status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    observed_at: datetime
    tasks: tuple[TaskRecoveryAssessment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_plan_shape(self) -> RunRecoveryPlan:
        task_ids: list[str] = []
        for task in self.tasks:
            if task.run_id != self.run_id:
                raise ValueError("recovery plan tasks must belong to the plan Run")
            if task.observed_at != self.observed_at:
                raise ValueError("recovery plan tasks must share one observation time")
            task_ids.append(task.task_id)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("recovery plan tasks must have unique task ids")
        return self
