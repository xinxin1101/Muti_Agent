from __future__ import annotations

import pytest

from app.models import (
    ContextUsage,
    MergeQueueSnapshot,
    RunEvent,
    TaskRunState,
)
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
