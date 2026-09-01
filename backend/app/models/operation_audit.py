from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperationAuditAction(StrEnum):
    PROJECT_ARCHIVED = "PROJECT_ARCHIVED"
    PROJECT_RESTORED = "PROJECT_RESTORED"
    PROJECT_DELETED = "PROJECT_DELETED"
    RUN_ARCHIVED = "RUN_ARCHIVED"
    RUN_RECOVERED = "RUN_RECOVERED"
    DEVELOPMENT_SESSION_CONTINUED = "DEVELOPMENT_SESSION_CONTINUED"
    DEVELOPMENT_SESSION_REPLANNED = "DEVELOPMENT_SESSION_REPLANNED"


class OperationAuditOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class OperationAuditRecord(BaseModel):
    """Append-only audit fact. It never authorizes the action it describes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: UUID
    operation_key: str = Field(min_length=1, max_length=255)
    actor: str = Field(min_length=1, max_length=128)
    action: OperationAuditAction
    outcome: OperationAuditOutcome
    project_id: UUID | None = None
    run_id: UUID | None = None
    development_session_id: UUID | None = None
    impact_summary: dict = Field(default_factory=dict)
    result_summary: str = Field(default="", max_length=512)
    created_at: datetime

    @model_validator(mode="after")
    def require_target(self) -> OperationAuditRecord:
        if self.project_id is None and self.run_id is None and self.development_session_id is None:
            raise ValueError("operation audit records require at least one target")
        return self
