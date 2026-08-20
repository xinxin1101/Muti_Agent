from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.dispatch import TaskDispatchReceipt
from app.models.dispatch_attempt import PersistedDispatchAttempt


class TaskReconciliationAction(StrEnum):
    """Server-side result of fresh locked task recovery revalidation."""

    NO_ACTION_RUN_TERMINAL = "NO_ACTION_RUN_TERMINAL"
    WAIT_ACTIVE_OWNER = "WAIT_ACTIVE_OWNER"
    RESUME_TERMINAL_EVIDENCE = "RESUME_TERMINAL_EVIDENCE"
    WAIT_EXISTING_DISPATCH = "WAIT_EXISTING_DISPATCH"
    BLOCKED_PUBLISH_FAILED = "BLOCKED_PUBLISH_FAILED"
    BLOCKED_RELEASED_EVIDENCE_GAP = "BLOCKED_RELEASED_EVIDENCE_GAP"
    PREPARED_DISPATCH = "PREPARED_DISPATCH"


class TaskReconciliationDecision(BaseModel):
    """Bounded decision produced while Run/Task recovery authority is locked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    action: TaskReconciliationAction
    reason: str = Field(min_length=1, max_length=1000)
    lease_generation: int = Field(ge=0)
    current_dispatch_id: UUID | None = None
    terminal_worker_evidence_id: int | None = Field(default=None, ge=1)
    dispatch_attempt: PersistedDispatchAttempt | None = None
    publish_allowed: bool = False
    recovery_attempt: bool = False

    @model_validator(mode="after")
    def validate_decision_shape(self) -> TaskReconciliationDecision:
        if self.action is TaskReconciliationAction.PREPARED_DISPATCH:
            if self.dispatch_attempt is None or not self.publish_allowed:
                raise ValueError(
                    "prepared reconciliation requires one publishable dispatch attempt"
                )
            if self.dispatch_attempt.run_id != self.run_id:
                raise ValueError(
                    "prepared dispatch Run identity must match reconciliation decision"
                )
            if self.dispatch_attempt.task_id != self.task_id:
                raise ValueError(
                    "prepared dispatch Task identity must match reconciliation decision"
                )
            if self.terminal_worker_evidence_id is not None:
                raise ValueError("prepared dispatch cannot coexist with terminal worker evidence")
            return self

        if self.publish_allowed:
            raise ValueError("only a newly PREPARED_DISPATCH may authorize broker publication")
        if self.recovery_attempt:
            raise ValueError("only a newly PREPARED_DISPATCH may be marked as a recovery attempt")
        return self


class TaskReconciliationOutcome(BaseModel):
    """Result returned by the reconciler after any authorized publication observation is durable."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: TaskReconciliationDecision
    receipt: TaskDispatchReceipt | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> TaskReconciliationOutcome:
        if self.receipt is None:
            return self
        if self.decision.action is not TaskReconciliationAction.PREPARED_DISPATCH:
            raise ValueError("only PREPARED_DISPATCH reconciliation may return a broker receipt")
        if self.receipt.run_id != self.decision.run_id:
            raise ValueError("reconciliation receipt Run identity mismatch")
        if self.receipt.task_id != self.decision.task_id:
            raise ValueError("reconciliation receipt Task identity mismatch")
        attempt = self.decision.dispatch_attempt
        if attempt is None or self.receipt.dispatch_id != attempt.dispatch_id:
            raise ValueError("reconciliation receipt dispatch identity mismatch")
        return self
