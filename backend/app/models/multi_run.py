from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.run import TaskRunState


class MultiTaskRunResult(BaseModel):
    """Terminal evidence for one persisted DAG Run.

    This model summarizes already-accepted worker/DAG/integration facts. It never derives task
    success from Agent text and cannot make a RUNNING task terminal by itself. A human-aborted task
    is deliberately distinct from a failed worker: its implementation may have succeeded while the
    integration conflict was explicitly rejected by the operator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    status: TaskRunState
    task_ids: tuple[str, ...] = Field(min_length=1)
    succeeded_task_ids: tuple[str, ...] = ()
    failed_task_ids: tuple[str, ...] = ()
    aborted_task_ids: tuple[str, ...] = ()
    blocked_task_ids: tuple[str, ...] = ()
    abort_evidence_fingerprints: tuple[str, ...] = ()
    integration_head: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    merge_evidence_id: int | None = Field(default=None, ge=1)
    merge_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> MultiTaskRunResult:
        if self.status not in {TaskRunState.SUCCEEDED, TaskRunState.FAILED}:
            raise ValueError("MultiTaskRunResult status must be terminal")
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError("task_ids must be unique")

        groups = (
            self.succeeded_task_ids,
            self.failed_task_ids,
            self.aborted_task_ids,
            self.blocked_task_ids,
        )
        for values in groups:
            if len(values) != len(set(values)):
                raise ValueError("terminal task groups must not contain duplicates")
        succeeded = set(self.succeeded_task_ids)
        failed = set(self.failed_task_ids)
        aborted = set(self.aborted_task_ids)
        blocked = set(self.blocked_task_ids)
        all_groups = (succeeded, failed, aborted, blocked)
        for index, left in enumerate(all_groups):
            if any(left & right for right in all_groups[index + 1 :]):
                raise ValueError("terminal task groups must be disjoint")
        if succeeded | failed | aborted | blocked != set(self.task_ids):
            raise ValueError("terminal task groups must cover the complete DAG")

        if len(self.abort_evidence_fingerprints) != len(self.aborted_task_ids):
            raise ValueError("each aborted task requires one human-decision evidence fingerprint")
        if len(self.abort_evidence_fingerprints) != len(set(self.abort_evidence_fingerprints)):
            raise ValueError("abort evidence fingerprints must be unique")
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in self.abort_evidence_fingerprints
        ):
            raise ValueError("abort evidence fingerprints must be lowercase SHA-256 values")

        merge_fields = (self.merge_evidence_id, self.merge_evidence_sha256)
        if (merge_fields[0] is None) != (merge_fields[1] is None):
            raise ValueError("merge evidence id and digest must be present together")

        if self.status is TaskRunState.SUCCEEDED:
            if failed or aborted or blocked or succeeded != set(self.task_ids):
                raise ValueError("successful DAG Runs require every task to succeed")
            if self.abort_evidence_fingerprints:
                raise ValueError("successful DAG Runs cannot contain human abort evidence")
            if self.integration_head is None or self.merge_evidence_id is None:
                raise ValueError("successful DAG Runs require complete integration evidence")
        elif not failed and not aborted:
            raise ValueError("failed DAG Runs require a failed worker or explicit human abort")
        return self
