from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.developer import DeveloperRunResult
from app.models.verification import VerificationResult

_OID_PATTERN = r"^[0-9a-f]{40,64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class IntegrationConflictRepairEvidence(BaseModel):
    """Accepted evidence for one human-authorized merge-conflict repair.

    The repair commit is not equivalent to a naturally clean merge. It becomes usable only when
    this typed evidence, the exact Git parent chain, the Human Gate decision, and deterministic
    verification all remain reproducible.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    integration_head: str = Field(pattern=_OID_PATTERN)
    task_commit: str = Field(pattern=_OID_PATTERN)
    conflict_marker_commit: str = Field(pattern=_OID_PATTERN)
    conflict_evidence_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    policy_fingerprint: str = Field(pattern=_SHA256_PATTERN)
    human_decision_commit: str = Field(pattern=_OID_PATTERN)
    conflicting_paths: tuple[str, ...] = Field(min_length=1)
    repair_commit: str = Field(pattern=_OID_PATTERN)
    changed_files: tuple[str, ...] = Field(min_length=1)
    developer_run: DeveloperRunResult
    verification: VerificationResult

    @model_validator(mode="after")
    def validate_repair_evidence(self) -> IntegrationConflictRepairEvidence:
        if len(self.conflicting_paths) != len(set(self.conflicting_paths)):
            raise ValueError("integration repair conflicting paths must be unique")
        if len(self.changed_files) != len(set(self.changed_files)):
            raise ValueError("integration repair changed files must be unique")
        if tuple(sorted(self.changed_files)) != self.changed_files:
            raise ValueError("integration repair changed files must be sorted")
        if not self.verification.passed:
            raise ValueError("accepted integration repair requires deterministic verification")
        return self
