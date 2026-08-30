from __future__ import annotations

import pytest

from app.models import (
    ContextUsage,
    MergeQueueSnapshot,
    RunEvent,
    TaskRunState,
    WorkflowExecutionMode,
    WorkflowExecutionRecord,
    WorkflowId,
    WorkflowMatch,
    WorkflowRoute,
)
from app.models.developer import DeveloperRunResult, DeveloperStopReason
from app.persistence import ContextFingerprintReference, PersistenceConfigurationError
from app.persistence.database import reveal_database_url
from app.persistence.serialization import canonical_payload, decode_evidence, verify_payload_hash
from app.persistence.types import PersistenceEvidenceKind


def test_database_url_requires_explicit_postgresql_psycopg_scheme() -> None:
    assert (
        reveal_database_url("postgresql+psycopg://user:pass@localhost/devflow")
        == "postgresql+psycopg://user:pass@localhost/devflow"
    )

    with pytest.raises(PersistenceConfigurationError, match=r"postgresql\+psycopg"):
        reveal_database_url("sqlite:///devflow.db")


def test_canonical_payload_hash_is_stable_and_round_trips_typed_evidence() -> None:
    event = RunEvent(sequence=3, state=TaskRunState.REVIEWING, detail="Review started.")

    first_payload, first_digest = canonical_payload(event)
    second_payload, second_digest = canonical_payload(event)

    assert first_payload == second_payload
    assert first_digest == second_digest
    verify_payload_hash(first_payload, first_digest, label="test event")
    assert decode_evidence(PersistenceEvidenceKind.STATE_TRANSITION, first_payload) == event


def test_context_reference_contains_identity_not_repository_source_content() -> None:
    reference = ContextFingerprintReference(
        task_id="TASK-CTX",
        stage="reviewer",
        fingerprint="a" * 64,
        repository_head="b" * 40,
        changed_files=("src/service.py",),
        selection_strategy="python_ast_import_relevance_v1",
        snippet_strategy="python_ast_symbol_regions_v1+deterministic_prefix_fallback",
        token_estimator="utf8_bytes_upper_bound",
        usage=ContextUsage(
            candidate_files=3,
            selected_files=2,
            selected_chars=450,
            estimated_tokens=500,
            truncated_files=0,
            omitted_files=1,
        ),
    )

    payload, digest = canonical_payload(reference)

    assert payload["fingerprint"] == "a" * 64
    assert "selected_files" not in payload
    assert "snippets" not in payload
    verify_payload_hash(payload, digest, label="context reference")
    assert decode_evidence(PersistenceEvidenceKind.CONTEXT_REFERENCE, payload) == reference


def test_run_level_merge_snapshot_round_trips_without_task_binding() -> None:
    snapshot = MergeQueueSnapshot(
        integration_ref="refs/heads/integration/run-1",
        run_base_commit="c" * 40,
        head_commit="c" * 40,
        integrated_task_ids=(),
        attempts=(),
        stopped=False,
    )

    payload, _ = canonical_payload(snapshot)

    assert decode_evidence(PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT, payload) == snapshot


def test_workflow_match_round_trips_as_typed_evidence() -> None:
    match = WorkflowMatch(
        task_id="hello-python",
        route=WorkflowRoute.WORKFLOW_CANDIDATE,
        workflow_id=WorkflowId.PYTHON_SCRIPT,
        confidence=0.91,
        matched_rules=("writable_files:python", "verification_commands:python"),
    )

    payload, _ = canonical_payload(match)

    assert decode_evidence(PersistenceEvidenceKind.WORKFLOW_MATCH, payload) == match


def test_workflow_execution_record_round_trips_as_typed_evidence() -> None:
    record = WorkflowExecutionRecord(
        task_id="hello-python",
        mode=WorkflowExecutionMode.WORKFLOW,
        workflow_id=WorkflowId.PYTHON_SCRIPT,
        attempts=1,
    )

    payload, _ = canonical_payload(record)

    assert decode_evidence(PersistenceEvidenceKind.WORKFLOW_EXECUTION, payload) == record


def test_developer_evidence_v1_remains_readable_after_budget_schema_upgrade() -> None:
    legacy_payload = {
        "stop_reason": DeveloperStopReason.MODEL_STOP.value,
        "iterations": 1,
        "tool_calls": 0,
        "changed_files": [],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "latency_ms": 0,
    }

    decoded = decode_evidence(
        PersistenceEvidenceKind.DEVELOPER_RUN,
        legacy_payload,
        schema_version=1,
    )

    assert isinstance(decoded, DeveloperRunResult)
    assert decoded.execution_budget is None
