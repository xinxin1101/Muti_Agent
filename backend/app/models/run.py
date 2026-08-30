from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import AgentRole, TokenUsage
from app.models.context import ContextContinuationState
from app.models.developer import DeveloperRunResult
from app.models.failure import FailureReport
from app.models.repair import RepairRunResult
from app.models.review import ReviewDecision
from app.models.verification import VerificationResult
from app.models.workflow import WorkflowExecutionRecord


class TaskRunState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    REPAIRING = "REPAIRING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RunEvent(BaseModel):
    """One auditable state-machine transition in a single-task run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    state: TaskRunState
    detail: str = Field(min_length=1, max_length=2000)


class AgentUsageSummary(BaseModel):
    """Measured usage for an Agent execution path that exposes runtime accounting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    model: str = Field(min_length=1, max_length=500)
    calls: int = Field(default=0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(default=0, ge=0)
    enable_thinking: bool = False


class SingleTaskRunResult(BaseModel):
    """Terminal evidence bundle for one V0.1 task execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    status: TaskRunState
    events: list[RunEvent] = Field(min_length=2)
    developer: DeveloperRunResult | None = None
    verifications: list[VerificationResult] = Field(default_factory=list)
    reviews: list[ReviewDecision] = Field(default_factory=list)
    repairs: list[RepairRunResult] = Field(default_factory=list)
    failures: list[FailureReport] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    repair_attempts: int = Field(default=0, ge=0)
    agent_models: dict[AgentRole, str] = Field(default_factory=dict)
    agent_usage: list[AgentUsageSummary] = Field(default_factory=list)
    context_state: ContextContinuationState | None = None
    workflow_execution: WorkflowExecutionRecord | None = None

    @model_validator(mode="after")
    def validate_terminal_consistency(self) -> SingleTaskRunResult:
        if self.status not in {TaskRunState.SUCCEEDED, TaskRunState.FAILED}:
            raise ValueError("SingleTaskRunResult status must be terminal")
        if self.events[-1].state is not self.status:
            raise ValueError("final run event must match terminal status")
        if self.status is TaskRunState.SUCCEEDED and self.failures:
            raise ValueError("successful runs must not contain terminal failures")
        if self.status is TaskRunState.FAILED and not self.failures:
            raise ValueError("failed runs require at least one terminal failure")
        if self.repair_attempts != len(self.repairs):
            raise ValueError("repair_attempts must equal the number of repair run records")
        if any(not model.strip() for model in self.agent_models.values()):
            raise ValueError("agent model identifiers must not be empty")
        return self
