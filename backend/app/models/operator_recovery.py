from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.run_reconciliation import DAGRunReconciliationPlan


class OperatorRecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OperatorActionKind(StrEnum):
    ADVANCE_RUN = "ADVANCE_RUN"


class OperatorAction(OperatorRecoveryModel):
    action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: OperatorActionKind
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=512)


class OperatorRecoveryPlan(OperatorRecoveryModel):
    run_id: UUID
    diagnostic_only: Literal[True] = True
    mutation_requires_fresh_revalidation: Literal[True] = True
    reconciliation: DAGRunReconciliationPlan
    actions: tuple[OperatorAction, ...] = ()

    @model_validator(mode="after")
    def validate_plan(self) -> OperatorRecoveryPlan:
        if self.reconciliation.run_id != self.run_id:
            raise ValueError("operator recovery plan must match reconciliation Run identity")
        action_ids = [item.action_id for item in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("operator actions must have unique action ids")
        return self


class OperatorActionRequestEvidence(OperatorRecoveryModel):
    run_id: UUID
    action_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: OperatorActionKind
    actor: Literal["product-operator"] = "product-operator"
    source: Literal["OPERATOR_SURFACE"] = "OPERATOR_SURFACE"
    fresh_revalidation_required: Literal[True] = True


class OperatorActionExecutionResult(OperatorRecoveryModel):
    run_id: UUID
    action: OperatorAction
    request_evidence_id: int = Field(ge=1)
    execution_delegated: Literal[True] = True
    refreshed_plan: OperatorRecoveryPlan

    @model_validator(mode="after")
    def validate_result(self) -> OperatorActionExecutionResult:
        if self.action.kind is not OperatorActionKind.ADVANCE_RUN:
            raise ValueError("unsupported operator action result")
        if self.refreshed_plan.run_id != self.run_id:
            raise ValueError("refreshed operator plan must match execution Run")
        return self
