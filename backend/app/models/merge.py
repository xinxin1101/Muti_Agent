from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.failure import FailureReport, FailureType

_OID_PATTERN = r"^[0-9a-f]{40,64}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class MergeAttemptOutcome(StrEnum):
    INTEGRATED = "INTEGRATED"
    CONFLICT = "CONFLICT"
    REPAIRED = "REPAIRED"


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
    conflict_marker_commit: str | None = Field(default=None, pattern=_OID_PATTERN)
    conflict_evidence_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    policy_fingerprint: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    human_decision_commit: str | None = Field(default=None, pattern=_OID_PATTERN)

    @model_validator(mode="after")
    def validate_outcome_consistency(self) -> MergeQueueAttempt:
        repair_fields = (
            self.conflict_marker_commit,
            self.conflict_evidence_fingerprint,
            self.policy_fingerprint,
            self.human_decision_commit,
        )
        if self.outcome is MergeAttemptOutcome.INTEGRATED:
            if self.integration_commit is None:
                raise ValueError("integrated merge attempts require an integration commit")
            if self.failure is not None:
                raise ValueError("integrated merge attempts must not contain failure evidence")
            if any(value is not None for value in repair_fields):
                raise ValueError("naturally clean integrations cannot contain repair metadata")
            return self

        if self.outcome is MergeAttemptOutcome.CONFLICT:
            if self.integration_commit is not None:
                raise ValueError("conflicted merge attempts must not advance integration commit")
            if self.failure is None or self.failure.failure_type is not FailureType.MERGE_CONFLICT:
                raise ValueError("conflicted merge attempts require MERGE_CONFLICT evidence")
            if any(value is not None for value in repair_fields):
                raise ValueError("unresolved conflict attempts cannot claim repair metadata")
            return self

        if self.integration_commit is None:
            raise ValueError("repaired merge attempts require an integration commit")
        if self.failure is not None:
            raise ValueError("repaired merge attempts must not contain failure evidence")
        if any(value is None for value in repair_fields):
            raise ValueError("repaired merge attempts require complete Human Gate metadata")
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
            if attempt.outcome in {MergeAttemptOutcome.INTEGRATED, MergeAttemptOutcome.REPAIRED}
        )
        if integrated_attempts != self.integrated_task_ids:
            raise ValueError("integrated task ids must match successful merge attempts")

        completed: set[str] = set()
        pending_conflict: MergeQueueAttempt | None = None
        for attempt in self.attempts:
            if attempt.outcome is MergeAttemptOutcome.CONFLICT:
                if pending_conflict is not None:
                    raise ValueError("a second conflict cannot begin before the first is resolved")
                if attempt.task_id in completed:
                    raise ValueError("an already integrated task cannot conflict again")
                pending_conflict = attempt
                continue

            if attempt.outcome is MergeAttemptOutcome.REPAIRED:
                if pending_conflict is None or pending_conflict.task_id != attempt.task_id:
                    raise ValueError("repaired integration must immediately resolve its task conflict")
                if (
                    pending_conflict.previous_integration_commit
                    != attempt.previous_integration_commit
                ):
                    raise ValueError("repair must chain from the same pre-conflict integration head")
                pending_conflict = None
            elif pending_conflict is not None:
                raise ValueError("unresolved conflict must be repaired before another task integrates")

            if attempt.task_id in completed:
                raise ValueError("a task may be integrated only once")
            completed.add(attempt.task_id)

        expected_stopped = pending_conflict is not None
        if self.stopped != expected_stopped:
            raise ValueError("merge queue stopped flag must match unresolved terminal conflict")
        if pending_conflict is not None and self.attempts[-1] is not pending_conflict:
            raise ValueError("unresolved merge conflict must be the final queue attempt")

        expected_head = self.run_base_commit
        for attempt in self.attempts:
            if attempt.previous_integration_commit != expected_head:
                raise ValueError("merge attempt must chain from the previous integration head")
            if attempt.outcome in {MergeAttemptOutcome.INTEGRATED, MergeAttemptOutcome.REPAIRED}:
                expected_head = attempt.integration_commit or expected_head
        if self.head_commit != expected_head:
            raise ValueError("snapshot head must match the last successful integration commit")
        return self
