from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.context import ContextPacket, ContextUsage
from app.models.run import TaskRunState
from app.models.task import TaskContract


class PersistenceEvidenceKind(StrEnum):
    STATE_TRANSITION = "STATE_TRANSITION"
    DEVELOPER_RUN = "DEVELOPER_RUN"
    VERIFICATION_RESULT = "VERIFICATION_RESULT"
    REVIEW_DECISION = "REVIEW_DECISION"
    REPAIR_RUN = "REPAIR_RUN"
    FAILURE_REPORT = "FAILURE_REPORT"
    MERGE_QUEUE_SNAPSHOT = "MERGE_QUEUE_SNAPSHOT"
    MERGE_CONFLICT = "MERGE_CONFLICT"
    INTEGRATION_GATE = "INTEGRATION_GATE"
    HUMAN_DECISION = "HUMAN_DECISION"
    INTEGRATION_REPAIR = "INTEGRATION_REPAIR"
    CONTEXT_REFERENCE = "CONTEXT_REFERENCE"
    DISPATCH_EVENT = "DISPATCH_EVENT"
    WORKER_EXECUTION = "WORKER_EXECUTION"
    TRACE_BATCH = "TRACE_BATCH"
    OPERATOR_ACTION = "OPERATOR_ACTION"
    TASK_CHECKPOINT = "TASK_CHECKPOINT"
    WORKFLOW_MATCH = "WORKFLOW_MATCH"
    WORKFLOW_EXECUTION = "WORKFLOW_EXECUTION"


class PersistedRunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"

    @classmethod
    def from_task_state(cls, state: TaskRunState) -> PersistedRunStatus:
        if state is TaskRunState.SUCCEEDED:
            return cls.SUCCEEDED
        if state is TaskRunState.FAILED:
            return cls.FAILED
        raise ValueError("only terminal task states can finalize a persisted run")


class ContextFingerprintReference(BaseModel):
    """Durable context identity without persisting selected repository source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    stage: str = Field(min_length=1, max_length=64)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_files: tuple[str, ...] = ()
    selection_strategy: str = Field(min_length=1, max_length=512)
    snippet_strategy: str = Field(min_length=1, max_length=512)
    token_estimator: str = Field(min_length=1, max_length=128)
    usage: ContextUsage

    @classmethod
    def from_packet(cls, packet: ContextPacket, *, stage: str) -> ContextFingerprintReference:
        return cls(
            task_id=packet.task_id,
            stage=stage,
            fingerprint=packet.fingerprint,
            repository_head=packet.repository_head,
            changed_files=tuple(packet.changed_files),
            selection_strategy=packet.selection_strategy,
            snippet_strategy=packet.snippet_strategy,
            token_estimator=packet.token_estimator,
            usage=packet.usage,
        )


class PersistedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: TaskContract
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class PersistedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    run_id: UUID
    task_id: str | None = Field(default=None, max_length=128)
    evidence_key: str = Field(min_length=1, max_length=255)
    kind: PersistenceEvidenceKind
    stage: str | None = Field(default=None, max_length=64)
    sequence: int | None = Field(default=None, ge=0)
    schema_version: int = Field(ge=1)
    payload: dict[str, Any]
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class PersistedRunSnapshot(BaseModel):
    """Read model for deterministic persistence recovery and audit queries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    project_id: UUID
    repository_url: str = Field(min_length=1, max_length=2000)
    default_branch: str = Field(min_length=1, max_length=255)
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    status: PersistedRunStatus
    tasks: tuple[PersistedTask, ...] = Field(min_length=1)
    evidence: tuple[PersistedEvidence, ...] = ()
    terminal_result: dict[str, Any] | None = None
    terminal_result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    finished_at: datetime | None = None

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> PersistedRunSnapshot:
        task_ids = [item.task.task_id for item in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("persisted run tasks must have unique task ids")

        if self.status is PersistedRunStatus.RUNNING:
            if self.terminal_result is not None or self.terminal_result_sha256 is not None:
                raise ValueError("running persisted runs must not contain terminal result evidence")
            if self.finished_at is not None:
                raise ValueError("running persisted runs must not have finished_at")
            return self

        if self.terminal_result is None or self.terminal_result_sha256 is None:
            raise ValueError("terminal persisted runs require terminal result evidence")
        if self.finished_at is None:
            raise ValueError("terminal persisted runs require finished_at")
        return self
