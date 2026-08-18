from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_OID_PATTERN = r"^[0-9a-fA-F]{40,64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class IntegrationPolicyRoute(StrEnum):
    AUTO_REPAIR_CANDIDATE = "AUTO_REPAIR_CANDIDATE"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class HumanGateDecision(StrEnum):
    AUTHORIZE_REPAIR = "AUTHORIZE_REPAIR"
    ABORT = "ABORT"


class IntegrationGateState(StrEnum):
    AUTO_REPAIR_CANDIDATE = "AUTO_REPAIR_CANDIDATE"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    REPAIR_AUTHORIZED = "REPAIR_AUTHORIZED"
    ABORTED = "ABORTED"


class IntegrationPolicyDecision(BaseModel):
    """Deterministic routing decision derived from structured conflict evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: IntegrationPolicyRoute
    evidence_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    automatic_repair_enabled: bool
    conflicting_paths: tuple[str, ...] = Field(min_length=1)
    reasons: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_route(self) -> IntegrationPolicyDecision:
        if len(self.conflicting_paths) != len(set(self.conflicting_paths)):
            raise ValueError("policy conflicting paths must be unique")
        if (
            self.route is IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE
            and not self.automatic_repair_enabled
        ):
            raise ValueError("automatic repair candidate requires the policy to be enabled")
        return self


class HumanIntegrationDecision(BaseModel):
    """Durable explicit human decision recorded against one conflict marker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HumanGateDecision
    actor: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=512)
    decision_ref: str = Field(min_length=1, max_length=512)
    decision_commit: str = Field(pattern=_OID_PATTERN)
    evidence_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    conflict_marker_commit: str = Field(pattern=_OID_PATTERN)

    @field_validator("actor", "note")
    @classmethod
    def require_single_line_metadata(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("human decision metadata must be single-line")
        return value


class IntegrationGateSnapshot(BaseModel):
    """Inspectable gate state. Step 2.7 never authorizes integration-ref advancement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    task_branch: str = Field(min_length=1, max_length=512)
    task_commit: str = Field(pattern=_OID_PATTERN)
    integration_ref: str = Field(min_length=1, max_length=512)
    integration_head: str = Field(pattern=_OID_PATTERN)
    conflict_ref: str = Field(min_length=1, max_length=512)
    conflict_marker_commit: str = Field(pattern=_OID_PATTERN)
    evidence_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    policy: IntegrationPolicyDecision
    state: IntegrationGateState
    human_decision: HumanIntegrationDecision | None = None
    repair_may_start: bool = False
    integration_may_advance: Literal[False] = False

    @model_validator(mode="after")
    def validate_state(self) -> IntegrationGateSnapshot:
        if self.policy.evidence_fingerprint != self.evidence_fingerprint:
            raise ValueError("policy and gate must reference the same conflict evidence")

        if self.state is IntegrationGateState.AUTO_REPAIR_CANDIDATE:
            if self.policy.route is not IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE:
                raise ValueError("auto-repair candidate state requires matching policy route")
            if self.human_decision is not None:
                raise ValueError("auto-repair candidate state must not contain a human decision")
            if not self.repair_may_start:
                raise ValueError("auto-repair candidate state must permit a later repair attempt")
            return self

        if self.policy.route is not IntegrationPolicyRoute.HUMAN_REQUIRED:
            raise ValueError("human gate states require HUMAN_REQUIRED policy route")

        if self.state is IntegrationGateState.AWAITING_HUMAN:
            if self.human_decision is not None:
                raise ValueError("awaiting-human state must not contain a human decision")
            if self.repair_may_start:
                raise ValueError("repair must remain blocked while awaiting a human decision")
            return self

        if self.human_decision is None:
            raise ValueError("terminal human gate states require a durable human decision")
        if self.human_decision.evidence_fingerprint != self.evidence_fingerprint:
            raise ValueError("human decision must reference the same conflict evidence")
        if self.human_decision.conflict_marker_commit != self.conflict_marker_commit:
            raise ValueError("human decision must reference the same conflict marker")

        expected = {
            IntegrationGateState.REPAIR_AUTHORIZED: HumanGateDecision.AUTHORIZE_REPAIR,
            IntegrationGateState.ABORTED: HumanGateDecision.ABORT,
        }[self.state]
        if self.human_decision.decision is not expected:
            raise ValueError("human decision is inconsistent with integration gate state")
        if self.repair_may_start != (expected is HumanGateDecision.AUTHORIZE_REPAIR):
            raise ValueError("repair permission must match the explicit human decision")
        return self
