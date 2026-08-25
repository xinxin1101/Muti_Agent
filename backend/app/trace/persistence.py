from __future__ import annotations

from app.models.events import (
    RuntimeEventDraft,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.persistence.types import PersistenceEvidenceKind


class TraceAwarePostgresEvidenceStore(ProjectAwarePostgresEvidenceStore):
    """Use the accepted evidence transaction path with one diagnostic event-source extension."""

    @classmethod
    def _evidence_event_draft(
        cls,
        *,
        row,
        task,
        kind: PersistenceEvidenceKind,
    ) -> RuntimeEventDraft:
        if kind is not PersistenceEvidenceKind.TRACE_BATCH:
            return super()._evidence_event_draft(row=row, task=task, kind=kind)

        generation = (
            task.lease_generation
            if task is not None and task.lease_generation
            else None
        )
        dispatch_id = task.lease_dispatch_id if task is not None else None
        return RuntimeEventDraft(
            event_key=f"evidence:{row.id}",
            kind=RuntimeEventKind.EVIDENCE_RECORDED,
            source=RuntimeEventSource.AGENT,
            level=RuntimeEventLevel.INFO,
            task_id=row.task_id,
            dispatch_id=dispatch_id,
            generation=generation,
            message="Diagnostic trace batch recorded.",
            attributes={
                "evidence_id": row.id,
                "evidence_key": row.evidence_key,
                "kind": kind.value,
                "stage": row.stage,
                "sequence": row.sequence,
                "payload_sha256": row.payload_sha256,
                "diagnostic_only": True,
                "privacy_mode": "METADATA_ONLY",
            },
        )
