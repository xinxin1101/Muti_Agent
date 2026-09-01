from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import AgentRole, LivenessCredit


class RunTokenBudgetStatus(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    EXHAUSTED = "EXHAUSTED"


class TokenBudgetStage(StrEnum):
    PLANNING = "PLANNING"
    DEVELOPMENT = "DEVELOPMENT"
    VERIFICATION_REPAIR = "VERIFICATION_REPAIR"
    REVIEW_PUBLICATION = "REVIEW_PUBLICATION"
    FLEX = "FLEX"


class TaskBudgetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RECLAIMED = "RECLAIMED"


class StageTokenBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: TokenBudgetStage
    total_budget_tokens: int = Field(ge=0)
    used_tokens: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)


class WorkPackageTokenBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    complexity: str = Field(pattern=r"^(LOW|MEDIUM|HIGH)$")
    total_budget_tokens: int = Field(ge=0)
    developer_budget_tokens: int = Field(ge=0)
    repair_budget_tokens: int = Field(ge=0)
    developer_used_tokens: int = Field(default=0, ge=0)
    repair_used_tokens: int = Field(default=0, ge=0)
    developer_reserved_tokens: int = Field(default=0, ge=0)
    repair_reserved_tokens: int = Field(default=0, ge=0)
    developer_borrowed_tokens: int = Field(default=0, ge=0)
    repair_borrowed_tokens: int = Field(default=0, ge=0)
    developer_reclaimed_tokens: int = Field(default=0, ge=0)
    repair_reclaimed_tokens: int = Field(default=0, ge=0)
    developer_observed_prompt_tokens: int = Field(default=0, ge=0)
    repair_observed_prompt_tokens: int = Field(default=0, ge=0)
    developer_predicted_next_input_tokens: int = Field(default=0, ge=0)
    repair_predicted_next_input_tokens: int = Field(default=0, ge=0)
    developer_estimated_executable_turns: int = Field(default=0, ge=0)
    repair_estimated_executable_turns: int = Field(default=0, ge=0)
    developer_startup_reserve_tokens: int = Field(default=0, ge=0)
    complexity_upgrade_count: int = Field(default=0, ge=0)
    borrow_count: int = Field(default=0, ge=0)
    tool_recovery_credit_used: bool = False
    last_liveness_credit: LivenessCredit = LivenessCredit.NORMAL
    last_required_tokens: int = Field(default=0, ge=0)
    last_available_tokens: int = Field(default=0, ge=0)
    last_flex_available_tokens: int = Field(default=0, ge=0)
    last_downstream_available_tokens: int = Field(default=0, ge=0)
    last_borrowed_tokens: int = Field(default=0, ge=0)
    last_budget_decision: str | None = Field(default=None, max_length=64)
    last_budget_reason: str | None = Field(default=None, max_length=512)
    last_recovery_action: str | None = Field(default=None, max_length=64)
    last_cost_prediction_reason: str | None = Field(default=None, max_length=512)
    status: TaskBudgetStatus = TaskBudgetStatus.ACTIVE


class RoleTokenUsage(BaseModel):
    """Durable per-role token counters for one Run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    call_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> RoleTokenUsage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("role token total must equal prompt plus completion tokens")
        return self


class RunTokenBudget(BaseModel):
    """Persisted reservation-aware token budget. It is a safety gate, not billing data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_budget_tokens: int = Field(ge=1)
    used_prompt_tokens: int = Field(default=0, ge=0)
    used_completion_tokens: int = Field(default=0, ge=0)
    used_total_tokens: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    status: RunTokenBudgetStatus = RunTokenBudgetStatus.NORMAL
    roles: tuple[RoleTokenUsage, ...] = ()
    stages: tuple[StageTokenBudget, ...] = ()
    work_packages: tuple[WorkPackageTokenBudget, ...] = ()

    @model_validator(mode="after")
    def validate_usage(self) -> RunTokenBudget:
        if self.used_total_tokens != self.used_prompt_tokens + self.used_completion_tokens:
            raise ValueError("run token total must equal prompt plus completion tokens")
        return self


class PlanningTokenBudget(BaseModel):
    """Auditable budget for the Planner before a Run identity exists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    launch_id: UUID
    total_budget_tokens: int = Field(ge=1)
    used_prompt_tokens: int = Field(default=0, ge=0)
    used_completion_tokens: int = Field(default=0, ge=0)
    used_total_tokens: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(ge=1)
    enable_thinking: bool = False
    status: RunTokenBudgetStatus = RunTokenBudgetStatus.NORMAL

    @model_validator(mode="after")
    def validate_usage(self) -> PlanningTokenBudget:
        if self.used_total_tokens != self.used_prompt_tokens + self.used_completion_tokens:
            raise ValueError("planning token total must equal prompt plus completion tokens")
        return self
