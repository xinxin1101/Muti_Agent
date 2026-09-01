from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.dag import TaskDAG


class DevelopmentSessionState(StrEnum):
    """Durable lifecycle before and across immutable Runs."""

    PLANNING = "PLANNING"
    PAUSED_PLANNING = "PAUSED_PLANNING"
    PLANNING_FAILED = "PLANNING_FAILED"
    READY_TO_RUN = "READY_TO_RUN"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


class DevelopmentWorkPackageState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    CHECKPOINTED = "CHECKPOINTED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class DevelopmentSessionBaselineState(StrEnum):
    UNCHANGED = "UNCHANGED"
    CHANGED = "CHANGED"


class DevelopmentSessionContinuationMode(StrEnum):
    AUTO = "AUTO"
    OLD_BASE = "OLD_BASE"


class DevelopmentSessionTimelineKind(StrEnum):
    """Bounded conversation entries derived from durable planning and Run facts."""

    USER_REQUIREMENT = "USER_REQUIREMENT"
    PLAN_DRAFT = "PLAN_DRAFT"
    BUDGET_DIAGNOSTIC = "BUDGET_DIAGNOSTIC"
    WORK_PACKAGE_SUCCEEDED = "WORK_PACKAGE_SUCCEEDED"
    WORK_PACKAGE_FAILED = "WORK_PACKAGE_FAILED"
    WORK_PACKAGE_CHECKPOINTED = "WORK_PACKAGE_CHECKPOINTED"
    RECOVERY_PREVIEW = "RECOVERY_PREVIEW"
    USER_ACTION = "USER_ACTION"
    RUN_LINKED = "RUN_LINKED"


class DevelopmentSessionCommandIntent(StrEnum):
    """Small allow-list for local conversation controls; this is never an agent tool."""

    CONTINUE_DEVELOPMENT = "CONTINUE_DEVELOPMENT"
    CONTINUE_OLD_BASE = "CONTINUE_OLD_BASE"
    REPLAN = "REPLAN"
    ARCHIVE_RUN = "ARCHIVE_RUN"
    ARCHIVE_PROJECT = "ARCHIVE_PROJECT"
    DELETE_PROJECT = "DELETE_PROJECT"
    UNKNOWN = "UNKNOWN"


class DevelopmentWorkPackageProgress(BaseModel):
    """Bounded session projection; Git and verification remain the authoritative evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    state: DevelopmentWorkPackageState
    source_run_id: UUID | None = None
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    completed_interfaces: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    verification_summary: str = Field(default="", max_length=512)
    failure_summary: str = Field(default="", max_length=512)
    remaining_budget_tokens: int | None = Field(default=None, ge=0)
    context_state: dict | None = None


class DevelopmentSession(BaseModel):
    """A recovery anchor that owns planning facts but never mutates historical Runs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    project_id: UUID
    requirement: str = Field(min_length=1, max_length=16_000)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    repository_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: DevelopmentSessionState
    dag: TaskDAG | None = None
    planning_diagnostic: str = Field(default="", max_length=1024)
    planning_launch_id: UUID | None = None
    latest_run_id: UUID | None = None
    resumed_from_run_id: UUID | None = None
    work_packages: tuple[DevelopmentWorkPackageProgress, ...] = ()
    created_at: datetime
    updated_at: datetime


class DevelopmentSessionTimelineEntry(BaseModel):
    """Read-only session conversation entry without credentials or raw model/tool content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_id: int = Field(ge=1)
    session_id: UUID
    kind: DevelopmentSessionTimelineKind
    title: str = Field(min_length=1, max_length=160)
    detail: str = Field(default="", max_length=512)
    run_id: UUID | None = None
    task_id: str | None = Field(default=None, max_length=128)
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
