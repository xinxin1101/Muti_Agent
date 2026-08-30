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
            draft = super()._evidence_event_draft(row=row, task=task, kind=kind)
            if kind not in {
                PersistenceEvidenceKind.DEVELOPER_RUN,
                PersistenceEvidenceKind.REPAIR_RUN,
                PersistenceEvidenceKind.TASK_CHECKPOINT,
            }:
                return draft
            if kind is PersistenceEvidenceKind.TASK_CHECKPOINT:
                checkpoint = row.payload if isinstance(row.payload, dict) else {}
                return draft.model_copy(
                    update={
                        "attributes": {
                            **draft.attributes,
                            "continuation": {
                                "slice_index": checkpoint.get("slice_index", 1),
                                "max_slices": checkpoint.get("max_slices", 1),
                                "elapsed_ms": checkpoint.get("elapsed_ms", 0),
                                "resume_from_commit": checkpoint.get("resume_from_commit"),
                                "remaining_summary": checkpoint.get("remaining_summary", ""),
                            },
                        }
                    }
                )
            return draft.model_copy(
                update={
                    "attributes": {
                        **draft.attributes,
                        "activity": cls._activity_summary(row.payload),
                    }
                }
            )

        generation = task.lease_generation if task is not None and task.lease_generation else None
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

    @staticmethod
    def _activity_summary(payload: object) -> dict[str, object]:
        """Project only bounded, browser-safe progress facts from typed agent evidence."""

        data = payload if isinstance(payload, dict) else {}
        changed_files = data.get("changed_files")
        safe_files = (
            [
                path
                for path in changed_files[:20]
                if isinstance(path, str) and path and len(path) <= 512
            ]
            if isinstance(changed_files, list)
            else []
        )
        return {
            "iterations": data.get("iterations", 0),
            "tool_calls": data.get("tool_calls", 0),
            "latency_ms": data.get("latency_ms", 0),
            "changed_files": safe_files,
            "changed_file_count": len(changed_files) if isinstance(changed_files, list) else 0,
        }
