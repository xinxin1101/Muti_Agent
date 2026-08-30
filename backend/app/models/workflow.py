from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowId(StrEnum):
    PYTHON_SCRIPT = "python-script"
    NODE_SCRIPT = "node-script"
    DEPENDENCY_PREFLIGHT = "dependency-preflight"
    VERIFICATION = "verification"
    GIT_PUBLICATION = "git-publication"


class WorkflowRoute(StrEnum):
    WORKFLOW_CANDIDATE = "WORKFLOW_CANDIDATE"
    AGENT_FALLBACK = "AGENT_FALLBACK"


class WorkflowExecutionMode(StrEnum):
    """The runtime route selected from the persisted deterministic match."""

    WORKFLOW = "WORKFLOW"
    AGENT = "AGENT"
    HYBRID = "HYBRID"


class WorkflowActivationMode(StrEnum):
    """Operator-selected rollout policy for deterministic task execution."""

    WORKFLOW_ONLY = "workflow_only"
    WORKFLOW_FIRST = "workflow_first"
    AGENT_ONLY = "agent_only"


class WorkflowStepStatus(StrEnum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class WorkflowDefinition(BaseModel):
    """A versioned, deterministic workflow capability registered by the platform."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: WorkflowId
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    deterministic_steps: tuple[str, ...] = Field(min_length=1, max_length=16)


class WorkflowMatch(BaseModel):
    """Pure-rule recommendation for one persisted Task; it does not authorize execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    route: WorkflowRoute
    execution_mode: WorkflowExecutionMode = WorkflowExecutionMode.AGENT
    workflow_id: WorkflowId | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    matched_rules: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    fallback_reason: str | None = Field(default=None, min_length=1, max_length=512)
    supporting_workflows: tuple[WorkflowId, ...] = ()

    @model_validator(mode="after")
    def validate_route_shape(self) -> WorkflowMatch:
        if self.route is WorkflowRoute.WORKFLOW_CANDIDATE:
            if self.workflow_id is None or self.fallback_reason is not None:
                raise ValueError("workflow candidates require a workflow and no fallback reason")
            return self
        if self.workflow_id is not None:
            raise ValueError("Agent fallback must not select a workflow")
        if self.fallback_reason is None:
            raise ValueError("Agent fallback requires a reason")
        return self


class WorkflowStepResult(BaseModel):
    """One deterministic operation; its output intentionally excludes credentials."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    status: WorkflowStepStatus
    detail: str = Field(min_length=1, max_length=512)
    attempt: int = Field(default=1, ge=1, le=3)


class WorkflowExecutionRecord(BaseModel):
    """Durable, resumable evidence for a deterministic Workflow attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    mode: WorkflowExecutionMode
    workflow_id: WorkflowId | None = None
    attempts: int = Field(default=0, ge=0, le=3)
    steps: tuple[WorkflowStepResult, ...] = ()
    fallback_reason: str | None = Field(default=None, max_length=512)
    # This is a conservative configured completion-token estimate, never billing or provider
    # accounting. Actual provider usage remains in RunTokenBudget.
    estimated_tokens_saved: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_execution_shape(self) -> WorkflowExecutionRecord:
        if self.mode is WorkflowExecutionMode.WORKFLOW and self.workflow_id is None:
            raise ValueError("Workflow execution requires a workflow id")
        if self.mode is WorkflowExecutionMode.AGENT and self.workflow_id is not None:
            raise ValueError("Agent execution must not select a workflow id")
        return self
