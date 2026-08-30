from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.task import _normalize_non_empty_text, _normalize_scope_pattern


class PlanningComplexity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class WorkPackageActivationMode(StrEnum):
    """Safe rollout modes for work-package scheduling.

    ``legacy_dag`` retains execution behavior while recording planner boundaries;
    ``work_package_first`` enables package budgets and continuation facts; and only
    ``contract_gated`` makes a missing producer interface block a consumer.
    """

    LEGACY_DAG = "legacy_dag"
    WORK_PACKAGE_FIRST = "work_package_first"
    CONTRACT_GATED = "contract_gated"


class TaskBudgetAllocation(BaseModel):
    """A Planner recommendation; Phase 2 turns it into an enforceable sub-budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=128)
    recommended_token_budget: int = Field(ge=1_000, le=20_000)


class InterfaceContract(BaseModel):
    """Declared producer/consumer boundary derived before Developer execution starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    interface_id: str = Field(min_length=1, max_length=256)
    producer_package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=128)
    consumer_package_ids: tuple[str, ...] = ()


class PlanningComplexityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=128)
    complexity: PlanningComplexity
    score: int = Field(ge=0, le=100)
    reasons: tuple[str, ...] = Field(min_length=1, max_length=8)


class WorkPackage(BaseModel):
    """One independently verifiable, file-bounded Developer work package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=128)
    objective: str = Field(min_length=1, max_length=1_500)
    deliverable: str = Field(min_length=1, max_length=256)
    owned_paths: tuple[str, ...] = Field(min_length=1, max_length=5)
    readable_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    produces: tuple[str, ...] = Field(min_length=1, max_length=16)
    consumes: tuple[str, ...] = Field(max_length=16)
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=4)
    verification_commands: tuple[str, ...] = Field(min_length=1, max_length=3)
    estimated_complexity: PlanningComplexity
    recommended_token_budget: int = Field(ge=1_000, le=20_000)

    @field_validator("objective", "deliverable")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalize_non_empty_text(value)

    @field_validator("owned_paths", "readable_paths")
    @classmethod
    def normalize_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_scope_pattern(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("work package paths must not contain duplicates")
        return normalized

    @field_validator("produces", "consumes", "acceptance_criteria", "verification_commands")
    @classmethod
    def normalize_items(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_non_empty_text(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("work package declarations must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_boundaries(self) -> WorkPackage:
        if set(self.owned_paths) & set(self.readable_paths):
            raise ValueError("owned_paths and readable_paths must not overlap")
        if (
            self.estimated_complexity is PlanningComplexity.LOW
            and self.recommended_token_budget > 4_000
        ):
            raise ValueError("LOW work packages may not request more than 4000 tokens")
        return self


class WorkPackagePlan(BaseModel):
    """The untrusted first-stage Planner proposal, accepted only after structural validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    packages: tuple[WorkPackage, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_unique_packages_and_interfaces(self) -> WorkPackagePlan:
        package_ids = [item.package_id for item in self.packages]
        if len(package_ids) != len(set(package_ids)):
            raise ValueError("work package ids must be unique")
        produced = [interface for item in self.packages for interface in item.produces]
        if len(produced) != len(set(produced)):
            raise ValueError("each produced interface must have exactly one producer")
        return self
