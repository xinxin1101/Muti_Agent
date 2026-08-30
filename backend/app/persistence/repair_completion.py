from __future__ import annotations

from pydantic import ValidationError

from app.models.integration_gate import HumanGateDecision, HumanIntegrationDecision
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.multi_run import MultiTaskRunResult
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.models import EvidenceRow
from app.persistence.multi_completion import PostgresMultiTaskCompletionStore
from app.persistence.serialization import verify_payload_hash
from app.persistence.types import PersistenceEvidenceKind


class RepairAwarePostgresMultiTaskCompletionStore(PostgresMultiTaskCompletionStore):
    """Phase 6 completion authority for DAG Runs containing repaired integrations."""

    @classmethod
    def _validate_success(
        cls,
        *,
        result: MultiTaskRunResult,
        dag,
        evidence_rows: list[EvidenceRow],
        worker_evidence,
    ) -> None:
        super()._validate_success(
            result=result,
            dag=dag,
            evidence_rows=evidence_rows,
            worker_evidence=worker_evidence,
        )
        if result.merge_evidence_id is None:
            raise PersistenceConflictError(
                "successful DAG finalization requires merge evidence identity"
            )
        merge_row = next(
            (item for item in evidence_rows if item.id == result.merge_evidence_id),
            None,
        )
        if merge_row is None:
            raise PersistenceConflictError("terminal merge evidence disappeared during validation")
        try:
            merge = MergeQueueSnapshot.model_validate(merge_row.payload)
        except ValidationError as exc:
            raise PersistenceCorruptionError(
                "terminal merge queue evidence failed repair-aware validation"
            ) from exc

        for attempt in merge.attempts:
            if attempt.outcome is MergeAttemptOutcome.REPAIRED:
                cls._require_repair_authority(
                    result=result,
                    attempt=attempt,
                    evidence_rows=evidence_rows,
                )

    @classmethod
    def _require_repair_authority(
        cls,
        *,
        result: MultiTaskRunResult,
        attempt: MergeQueueAttempt,
        evidence_rows: list[EvidenceRow],
    ) -> None:
        if (
            attempt.integration_commit is None
            or attempt.conflict_marker_commit is None
            or attempt.conflict_evidence_fingerprint is None
            or attempt.policy_fingerprint is None
            or attempt.human_decision_commit is None
        ):
            raise PersistenceCorruptionError(
                "repaired merge attempt lacks complete durable authority identity"
            )

        repair_matches: list[IntegrationConflictRepairEvidence] = []
        decision_matches: list[HumanIntegrationDecision] = []
        for row in evidence_rows:
            if row.task_id != attempt.task_id:
                continue
            if row.kind == PersistenceEvidenceKind.INTEGRATION_REPAIR.value:
                verify_payload_hash(
                    row.payload,
                    row.payload_sha256,
                    label=f"integration repair evidence {row.id}",
                )
                try:
                    repair = IntegrationConflictRepairEvidence.model_validate(row.payload)
                except ValidationError as exc:
                    raise PersistenceCorruptionError(
                        f"integration repair evidence {row.id} failed validation"
                    ) from exc
                if repair.conflict_evidence_fingerprint == attempt.conflict_evidence_fingerprint:
                    repair_matches.append(repair)
                continue

            if row.kind == PersistenceEvidenceKind.HUMAN_DECISION.value:
                verify_payload_hash(
                    row.payload,
                    row.payload_sha256,
                    label=f"human decision evidence {row.id}",
                )
                try:
                    decision = HumanIntegrationDecision.model_validate(row.payload)
                except ValidationError as exc:
                    raise PersistenceCorruptionError(
                        f"human decision evidence {row.id} failed validation"
                    ) from exc
                if decision.evidence_fingerprint == attempt.conflict_evidence_fingerprint:
                    decision_matches.append(decision)

        if len(repair_matches) != 1:
            raise PersistenceConflictError(
                f"repaired task {attempt.task_id!r} requires exactly one typed repair evidence"
            )
        if len(decision_matches) != 1:
            raise PersistenceConflictError(
                f"repaired task {attempt.task_id!r} requires exactly one human decision"
            )

        repair = repair_matches[0]
        expected_repair = {
            "run_id": result.run_id,
            "task_id": attempt.task_id,
            "integration_head": attempt.previous_integration_commit,
            "task_commit": attempt.task_commit,
            "conflict_marker_commit": attempt.conflict_marker_commit,
            "conflict_evidence_fingerprint": attempt.conflict_evidence_fingerprint,
            "policy_fingerprint": attempt.policy_fingerprint,
            "human_decision_commit": attempt.human_decision_commit,
            "repair_commit": attempt.integration_commit,
        }
        for field, expected in expected_repair.items():
            if getattr(repair, field) != expected:
                raise PersistenceConflictError(f"terminal repair evidence binding changed: {field}")

        decision = decision_matches[0]
        if decision.decision is not HumanGateDecision.AUTHORIZE_REPAIR:
            raise PersistenceConflictError(
                "repaired terminal integration is not backed by AUTHORIZE_REPAIR"
            )
        expected_decision = {
            "decision_commit": attempt.human_decision_commit,
            "evidence_fingerprint": attempt.conflict_evidence_fingerprint,
            "policy_fingerprint": attempt.policy_fingerprint,
            "conflict_marker_commit": attempt.conflict_marker_commit,
        }
        for field, expected in expected_decision.items():
            if getattr(decision, field) != expected:
                raise PersistenceConflictError(
                    f"terminal human repair decision binding changed: {field}"
                )
