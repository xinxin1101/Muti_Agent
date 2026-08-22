from __future__ import annotations

from app.models.events import (
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.persistence.repository import PostgresEvidenceStore
from app.persistence.types import PersistenceEvidenceKind


class OperatorAwarePostgresEvidenceStore(PostgresEvidenceStore):
    """Extend the accepted evidence transaction path for typed operator-request audit events."""

    @classmethod
    def _evidence_event_draft(
        cls,
        *,
        row,
        task,
        kind: PersistenceEvidenceKind,
    ) -> RuntimeEventDraft:
        if kind is not PersistenceEvidenceKind.OPERATOR_ACTION:
            return super()._evidence_event_draft(row=row, task=task, kind=kind)

        return RuntimeEventDraft(
            event_key=f"evidence:{row.id}",
            kind=RuntimeEventKind.EVIDENCE_RECORDED,
            source=RuntimeEventSource.RUNTIME,
            level=RuntimeEventLevel.INFO,
            task_id=None,
            dispatch_id=None,
            generation=None,
            message="Accepted OPERATOR_ACTION request evidence.",
            attributes={
                "evidence_id": row.id,
                "evidence_key": row.evidence_key,
                "evidence_kind": kind.value,
                "stage": row.stage,
                "evidence_sequence": row.sequence,
                "payload_sha256": row.payload_sha256,
                "request_only": True,
                "fresh_revalidation_required": True,
            },
        )
