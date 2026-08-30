from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, TypeAlias

from pydantic import BaseModel

from app.models.checkpoint import TaskCheckpoint
from app.models.conflict import MergeConflictEvidence
from app.models.developer import DeveloperRunResult
from app.models.dispatch import WorkerDispatchEvent, WorkerExecutionEvidence
from app.models.failure import FailureReport
from app.models.integration_gate import HumanIntegrationDecision, IntegrationGateSnapshot
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeQueueSnapshot
from app.models.multi_run import MultiTaskRunResult
from app.models.operator_recovery import OperatorActionRequestEvidence
from app.models.repair import RepairRunResult
from app.models.review import ReviewDecision
from app.models.run import RunEvent, SingleTaskRunResult
from app.models.trace import TaskTraceBatch
from app.models.verification import VerificationResult
from app.models.workflow import WorkflowExecutionRecord, WorkflowMatch
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import ContextFingerprintReference, PersistenceEvidenceKind

EvidenceModel: TypeAlias = (
    RunEvent
    | DeveloperRunResult
    | VerificationResult
    | ReviewDecision
    | RepairRunResult
    | FailureReport
    | MergeQueueSnapshot
    | MergeConflictEvidence
    | IntegrationGateSnapshot
    | HumanIntegrationDecision
    | IntegrationConflictRepairEvidence
    | ContextFingerprintReference
    | WorkerDispatchEvent
    | WorkerExecutionEvidence
    | TaskTraceBatch
    | OperatorActionRequestEvidence
    | TaskCheckpoint
    | WorkflowMatch
    | WorkflowExecutionRecord
)
TerminalRunResult: TypeAlias = SingleTaskRunResult | MultiTaskRunResult

_EVIDENCE_MODELS: dict[PersistenceEvidenceKind, type[BaseModel]] = {
    PersistenceEvidenceKind.STATE_TRANSITION: RunEvent,
    PersistenceEvidenceKind.DEVELOPER_RUN: DeveloperRunResult,
    PersistenceEvidenceKind.VERIFICATION_RESULT: VerificationResult,
    PersistenceEvidenceKind.REVIEW_DECISION: ReviewDecision,
    PersistenceEvidenceKind.REPAIR_RUN: RepairRunResult,
    PersistenceEvidenceKind.FAILURE_REPORT: FailureReport,
    PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT: MergeQueueSnapshot,
    PersistenceEvidenceKind.MERGE_CONFLICT: MergeConflictEvidence,
    PersistenceEvidenceKind.INTEGRATION_GATE: IntegrationGateSnapshot,
    PersistenceEvidenceKind.HUMAN_DECISION: HumanIntegrationDecision,
    PersistenceEvidenceKind.INTEGRATION_REPAIR: IntegrationConflictRepairEvidence,
    PersistenceEvidenceKind.CONTEXT_REFERENCE: ContextFingerprintReference,
    PersistenceEvidenceKind.DISPATCH_EVENT: WorkerDispatchEvent,
    PersistenceEvidenceKind.WORKER_EXECUTION: WorkerExecutionEvidence,
    PersistenceEvidenceKind.TRACE_BATCH: TaskTraceBatch,
    PersistenceEvidenceKind.OPERATOR_ACTION: OperatorActionRequestEvidence,
    PersistenceEvidenceKind.TASK_CHECKPOINT: TaskCheckpoint,
    PersistenceEvidenceKind.WORKFLOW_MATCH: WorkflowMatch,
    PersistenceEvidenceKind.WORKFLOW_EXECUTION: WorkflowExecutionRecord,
}

# The database column is the schema version of the JSON payload. Keep versions scoped to
# an evidence kind: adding a field to Developer evidence must not invalidate unrelated rows.
_CURRENT_SCHEMA_VERSION: dict[PersistenceEvidenceKind, int] = {kind: 1 for kind in _EVIDENCE_MODELS}
_CURRENT_SCHEMA_VERSION[PersistenceEvidenceKind.DEVELOPER_RUN] = 2


def evidence_schema_version(kind: PersistenceEvidenceKind) -> int:
    return _CURRENT_SCHEMA_VERSION[kind]


def canonical_payload(model: BaseModel) -> tuple[dict, str]:
    payload = model.model_dump(mode="json")
    return payload, payload_sha256(payload)


def payload_sha256(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def verify_payload_hash(payload: dict, expected_sha256: str, *, label: str) -> None:
    actual = payload_sha256(payload)
    if actual != expected_sha256:
        raise PersistenceCorruptionError(
            f"{label} payload hash mismatch: expected {expected_sha256}, got {actual}"
        )


def decode_evidence(
    kind: PersistenceEvidenceKind,
    payload: dict[str, Any],
    *,
    schema_version: int | None = None,
) -> EvidenceModel:
    version = schema_version if schema_version is not None else evidence_schema_version(kind)
    current_version = evidence_schema_version(kind)
    if version < 1 or version > current_version:
        raise PersistenceCorruptionError(
            f"unsupported {kind.value} payload schema version {version}; "
            f"this runtime supports up to {current_version}"
        )

    normalized_payload = dict(payload)
    # V1 Developer results predate the persisted effective execution budget. The field is
    # optional in the model, but making the migration explicit documents the durable contract
    # and preserves compatibility should validation become stricter later.
    if kind is PersistenceEvidenceKind.DEVELOPER_RUN and version == 1:
        normalized_payload.setdefault("execution_budget", None)

    model_type = _EVIDENCE_MODELS[kind]
    try:
        return model_type.model_validate(normalized_payload)
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted {kind.value} payload failed typed validation: {exc}"
        ) from exc


def decode_terminal_result(payload: dict) -> TerminalRunResult:
    single_error: ValueError | None = None
    try:
        return SingleTaskRunResult.model_validate(payload)
    except ValueError as exc:
        single_error = exc

    try:
        return MultiTaskRunResult.model_validate(payload)
    except ValueError as multi_error:
        raise PersistenceCorruptionError(
            "persisted terminal run result failed both single-task and multi-task typed "
            f"validation: single={single_error}; multi={multi_error}"
        ) from multi_error
