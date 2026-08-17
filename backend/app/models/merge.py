from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.failure import FailureReport, FailureType


class MergeAttemptOutcome(StrEnum):
    INTEGRATED = "INTEGRATED"
    CONFLICT = "CONFLICT"


class MergeQueueAttempt(BaseModel):
    """Auditable evidence for one task-branch integration attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    task_id: str = Field(min_length=1, max_length=128)
    task_branch: str = Field(min_length=1, max_length=512)
    task_base_commit: str = Field(min_length=40, max_length=64)
    task_commit: str = Field(min_length=40, max_length=64)
    previous_integration_commit: str = Field(min_length=40, max_length=64)
    outcome: MergeAttemptOutcome
    integration_commit: str | None = Field(default=None, min_length=40, max_length=64)
    failure: FailureReport | None = None

    @model_validator(mode="after")
    def validate_outcome_consistency(self) -> MergeQueueAttempt:
        if self.outcome is MergeAttemptOutcome.INTEGRATED:
            if self.integration_commit is None:
                raise ValueError("integrated merge attempts require an integration commit")
            if self.failure is not None:
                raise ValueError("integrated merge attempts must not contain failure evidence")
        else:
            if self.integration_commit is not None:
                raise ValueError("conflicted merge attempts must not advance integration commit")
            if self.failure is None or self.failure.failure_type is not FailureType.MERGE_CONFLICT:
                raise ValueError("conflicted merge attempts require MERGE_CONFLICT evidence")
        return self


class MergeQueueSnapshot(BaseModel):
    """Immutable state of one topological integration ref."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_ref: str = Field(min_length=1, max_length=512)
    run_base_commit: str = Field(min_length=40, max_length=64)
    head_commit: str = Field(min_length=40, max_length=64)
    integrated_task_ids: tuple[str, ...] = Field(default_factory=tuple)
    attempts: tuple[MergeQueueAttempt, ...] = Field(default_factory=tuple)
    stopped: bool = False

    @model_validator(mode="after")
    def validate_snapshot_consistency(self) -> MergeQueueSnapshot:
        if len(self.integrated_task_ids) != len(set(self.integrated_task_ids)):
            raise ValueError("integrated_task_ids must be unique")
        if any(attempt.sequence != index for index, attempt in enumerate(self.attempts)):
            raise ValueError("merge queue attempt sequence must be contiguous from zero")

        integrated_attempts = tuple(
            attempt.task_id
            for attempt in self.attempts
            if attempt.outcome is MergeAttemptOutcome.INTEGRATED
        )
        if integrated_attempts != self.integrated_task_ids:
            raise ValueError("integrated task ids must match successful merge attempts")

        conflict_attempts = [
            attempt for attempt in self.attempts if attempt.outcome is MergeAttemptOutcome.CONFLICT
        ]
        if len(conflict_attempts) > 1:
            raise ValueError("merge queue may contain at most one terminal conflict attempt")
        if self.stopped != bool(conflict_attempts):
            raise ValueError("merge queue stopped flag must match terminal conflict evidence")
        if conflict_attempts and self.attempts[-1] is not conflict_attempts[0]:
            raise ValueError("merge conflict must be the final queue attempt")

        expected_head = self.run_base_commit
        for attempt in self.attempts:
            if attempt.previous_integration_commit != expected_head:
                raise ValueError("merge attempt must chain from the previous integration head")
            if attempt.outcome is MergeAttemptOutcome.INTEGRATED:
                expected_head = attempt.integration_commit or expected_head
        if self.head_commit != expected_head:
            raise ValueError("snapshot head must match the last successful integration commit")
        return self
