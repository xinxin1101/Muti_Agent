from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.checkpoint import CheckpointReason, TaskCheckpoint
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.failure import FailureReport, FailureSource, FailureType
from app.persistence.serialization import decode_evidence
from app.persistence.types import PersistenceEvidenceKind


def _checkpoint(*, task_id: str = "gomoku-core") -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id=task_id,
        base_commit="a" * 40,
        commit_sha="b" * 40,
        changed_files=("gomoku/core.py",),
        reason=CheckpointReason.TIME_LIMIT,
        summary="已保存当前受控代码改动。",
    )


def test_checkpoint_is_typed_persistence_evidence() -> None:
    checkpoint = _checkpoint()
    decoded = decode_evidence(
        PersistenceEvidenceKind.TASK_CHECKPOINT,
        checkpoint.model_dump(mode="json"),
    )

    assert decoded == checkpoint


def test_failed_worker_may_carry_only_a_matching_checkpoint() -> None:
    checkpoint = _checkpoint()
    failed = WorkerExecutionEvidence(
        dispatch_id=uuid4(),
        run_id=uuid4(),
        task_id=checkpoint.task_id,
        status=WorkerExecutionStatus.FAILED,
        base_commit=checkpoint.base_commit,
        checkpoint=checkpoint,
        failures=(
            FailureReport(
                failure_type=FailureType.MODEL_TIMEOUT,
                source=FailureSource.RUNTIME,
                message="bounded developer stage ended",
                retryable=True,
            ),
        ),
        duration_ms=1,
    )
    assert failed.checkpoint == checkpoint

    with pytest.raises(ValidationError, match="must match the failed task"):
        WorkerExecutionEvidence(
            dispatch_id=uuid4(),
            run_id=uuid4(),
            task_id="other-task",
            status=WorkerExecutionStatus.FAILED,
            base_commit=checkpoint.base_commit,
            checkpoint=checkpoint,
            failures=(
                FailureReport(
                    failure_type=FailureType.MODEL_TIMEOUT,
                    source=FailureSource.RUNTIME,
                    message="bounded developer stage ended",
                    retryable=True,
                ),
            ),
            duration_ms=1,
        )
