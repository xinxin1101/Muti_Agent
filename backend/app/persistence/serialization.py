from __future__ import annotations

import json
from hashlib import sha256
from typing import TypeAlias

from pydantic import BaseModel

from app.models.conflict import MergeConflictEvidence
from app.models.developer import DeveloperRunResult
from app.models.dispatch import WorkerDispatchEvent, WorkerExecutionEvidence
from app.models.failure import FailureReport
from app.models.integration_gate import HumanIntegrationDecision, IntegrationGateSnapshot
from app.models.merge import MergeQueueSnapshot
from app.models.repair import RepairRunResult
from app.models.review import ReviewDecision
from app.models.run import RunEvent, SingleTaskRunResult
from app.models.verification import VerificationResult
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
    | ContextFingerprintReference
    | WorkerDispatchEvent
    | WorkerExecutionEvidence
)

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
    PersistenceEvidenceKind.CONTEXT_REFERENCE: ContextFingerprintReference,
    PersistenceEvidenceKind.DISPATCH_EVENT: WorkerDispatchEvent,
    PersistenceEvidenceKind.WORKER_EXECUTION: WorkerExecutionEvidence,
}


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


def decode_evidence(kind: PersistenceEvidenceKind, payload: dict) -> EvidenceModel:
    model_type = _EVIDENCE_MODELS[kind]
    try:
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted {kind.value} payload failed typed validation: {exc}"
        ) from exc


def decode_terminal_result(payload: dict) -> SingleTaskRunResult:
    try:
        return SingleTaskRunResult.model_validate(payload)
    except ValueError as exc:
        raise PersistenceCorruptionError(
            f"persisted terminal run result failed typed validation: {exc}"
        ) from exc
