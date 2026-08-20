from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest

import app.api.service as service_module
from app.api.app import create_app
from app.api.metrics import aggregate_runtime_events, build_run_metrics
from app.api.models import ProductRunMetrics
from app.api.service import ProductMetricsUnavailableError, ProductRuntimeService
from app.models.events import (
    PersistedRuntimeEvent,
    RuntimeEventKind,
    RuntimeEventLevel,
    RuntimeEventSource,
)
from app.models.task import TaskContract
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
STARTED = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)


def _task() -> PersistedTask:
    contract = TaskContract(
        task_id="task-1",
        objective="Measure accepted runtime work without deciding success.",
        readable_files=["src/**"],
        writable_files=["src/app.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Metrics remain descriptive."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )
    return PersistedTask(
        task=contract,
        contract_sha256="a" * 64,
        created_at=STARTED,
    )


def _typed_payload(kind: PersistenceEvidenceKind) -> dict[str, object]:
    if kind is PersistenceEvidenceKind.DEVELOPER_RUN:
        return {
            "stop_reason": "MODEL_STOP",
            "iterations": 2,
            "tool_calls": 1,
            "final_message": "done",
            "changed_files": ["src/app.py"],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "latency_ms": 12,
        }
    if kind is PersistenceEvidenceKind.REVIEW_DECISION:
        return {
            "decision": "CHANGES_REQUESTED",
            "summary": "One semantic correction is required.",
            "issues": [
                {
                    "severity": "medium",
                    "message": "Correct the semantic requirement.",
                    "file": "src/app.py",
                    "line": 1,
                }
            ],
        }
    if kind is PersistenceEvidenceKind.REPAIR_RUN:
        return {
            "attempt": 1,
            "failure_types": ["REVIEW_REJECTED"],
            "stop_reason": "MODEL_STOP",
            "iterations": 1,
            "tool_calls": 1,
            "final_message": "repaired",
            "changed_files": ["src/app.py"],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 2,
                "total_tokens": 5,
            },
            "latency_ms": 7,
        }
    if kind is PersistenceEvidenceKind.FAILURE_REPORT:
        return {
            "failure_type": "SCOPE_VIOLATION",
            "source": "verification",
            "message": "Protected test changed.",
            "retryable": False,
            "evidence": ["tests/test_app.py"],
        }
    return {"test": True}


def _evidence(evidence_id: int, kind: PersistenceEvidenceKind) -> PersistedEvidence:
    return PersistedEvidence(
        id=evidence_id,
        run_id=RUN_ID,
        task_id="task-1",
        evidence_key=f"metric:{evidence_id}",
        kind=kind,
        stage="test",
        schema_version=1,
        payload=_typed_payload(kind),
        payload_sha256="b" * 64,
        created_at=STARTED + timedelta(seconds=evidence_id),
    )


def _snapshot(*, terminal: bool = False) -> PersistedRunSnapshot:
    evidence_kinds = (
        PersistenceEvidenceKind.DEVELOPER_RUN,
        PersistenceEvidenceKind.VERIFICATION_RESULT,
        PersistenceEvidenceKind.VERIFICATION_RESULT,
        PersistenceEvidenceKind.VERIFICATION_RESULT,
        PersistenceEvidenceKind.REVIEW_DECISION,
        PersistenceEvidenceKind.REPAIR_RUN,
        PersistenceEvidenceKind.FAILURE_REPORT,
        PersistenceEvidenceKind.DISPATCH_EVENT,
        PersistenceEvidenceKind.WORKER_EXECUTION,
        PersistenceEvidenceKind.MERGE_CONFLICT,
        PersistenceEvidenceKind.HUMAN_DECISION,
    )
    finished = STARTED + timedelta(seconds=12, milliseconds=345) if terminal else None
    return PersistedRunSnapshot(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://example.com/repo.git",
        default_branch="main",
        base_commit="c" * 40,
        status=PersistedRunStatus.FAILED if terminal else PersistedRunStatus.RUNNING,
        tasks=(_task(),),
        evidence=tuple(
            _evidence(index, kind)
            for index, kind in enumerate(evidence_kinds, start=1)
        ),
        terminal_result={"status": "FAILED"} if terminal else None,
        terminal_result_sha256="d" * 64 if terminal else None,
        started_at=STARTED,
        finished_at=finished,
    )


def _event(
    sequence: int,
    *,
    run_id: UUID = RUN_ID,
    kind: RuntimeEventKind = RuntimeEventKind.EVIDENCE_RECORDED,
    level: RuntimeEventLevel = RuntimeEventLevel.INFO,
) -> PersistedRuntimeEvent:
    return PersistedRuntimeEvent(
        id=sequence,
        event_id=uuid4(),
        run_id=run_id,
        sequence=sequence,
        event_key=f"event:{sequence}",
        kind=kind,
        source=RuntimeEventSource.RUNTIME,
        level=level,
        task_id="task-1",
        dispatch_id=None,
        generation=None,
        message="Accepted runtime fact.",
        schema_version=1,
        attributes={},
        attributes_sha256="e" * 64,
        created_at=STARTED + timedelta(seconds=sequence),
    )


def test_metrics_count_work_without_deriving_success() -> None:
    events = (
        _event(1, kind=RuntimeEventKind.RUN_STARTED),
        _event(2, level=RuntimeEventLevel.WARNING),
        _event(3, kind=RuntimeEventKind.LEASE_ACQUIRED),
        _event(4, kind=RuntimeEventKind.LEASE_TAKEN_OVER),
        _event(5, kind=RuntimeEventKind.LEASE_RELEASED, level=RuntimeEventLevel.ERROR),
    )

    metrics = build_run_metrics(_snapshot(), aggregate_runtime_events(RUN_ID, events))
    payload = metrics.model_dump(mode="json")

    assert metrics.status is PersistedRunStatus.RUNNING
    assert metrics.status_basis.value == "PERSISTED_RUN"
    assert metrics.terminal_duration_ms is None
    assert metrics.evidence.verification_attempts == 3
    assert metrics.evidence.review_decisions == 1
    assert metrics.evidence.reviewer_rejections == 1
    assert metrics.evidence.repair_attempts == 1
    assert metrics.evidence.failure_reports == 1
    assert metrics.evidence.scope_violations == 1
    assert metrics.evidence.developer_prompt_tokens == 10
    assert metrics.evidence.developer_completion_tokens == 5
    assert metrics.evidence.developer_total_tokens == 15
    assert metrics.evidence.repair_prompt_tokens == 3
    assert metrics.evidence.repair_completion_tokens == 2
    assert metrics.evidence.repair_total_tokens == 5
    assert metrics.evidence.reviewer_token_usage_available is False
    assert metrics.evidence.estimated_cost_available is False
    assert metrics.runtime_events.total_events == 5
    assert metrics.runtime_events.warning_events == 1
    assert metrics.runtime_events.error_events == 1
    assert metrics.runtime_events.lease_acquisitions == 1
    assert metrics.runtime_events.lease_takeovers == 1
    assert metrics.runtime_events.lease_releases == 1
    assert "success_rate" not in payload
    assert "pass_rate" not in payload
    assert "approval_rate" not in payload
    assert "score" not in payload


def test_metrics_fail_closed_on_inconsistent_typed_token_usage() -> None:
    snapshot = _snapshot()
    evidence = list(snapshot.evidence)
    developer = evidence[0]
    evidence[0] = developer.model_copy(
        update={
            "payload": {
                **developer.payload,
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 999,
                },
            }
        }
    )
    corrupted = snapshot.model_copy(update={"evidence": tuple(evidence)})

    with pytest.raises(PersistenceCorruptionError, match="token usage"):
        build_run_metrics(corrupted, aggregate_runtime_events(RUN_ID, ()))


def test_terminal_duration_uses_only_persisted_timestamps() -> None:
    metrics = build_run_metrics(
        _snapshot(terminal=True),
        aggregate_runtime_events(RUN_ID, ()),
    )

    assert metrics.status is PersistedRunStatus.FAILED
    assert metrics.terminal_duration_ms == 12_345


def test_runtime_event_gap_or_cross_run_fails_closed() -> None:
    with pytest.raises(PersistenceCorruptionError, match="contiguous"):
        aggregate_runtime_events(RUN_ID, (_event(1), _event(3)))

    with pytest.raises(PersistenceCorruptionError, match="different Run"):
        aggregate_runtime_events(RUN_ID, (_event(1, run_id=uuid4()),))


class FakeStore:
    def __init__(
        self,
        snapshot: PersistedRunSnapshot,
        events: tuple[PersistedRuntimeEvent, ...],
    ) -> None:
        self.snapshot = snapshot
        self.events = events

    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot:
        assert run_id == RUN_ID
        return self.snapshot

    async def list_runtime_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> tuple[PersistedRuntimeEvent, ...]:
        assert run_id == RUN_ID
        return tuple(
            event for event in self.events if event.sequence > after_sequence
        )[:limit]

    async def dispose(self) -> None:
        return None


class NotUsed:
    async def dispose(self) -> None:
        return None

    def __getattr__(self, name: str):
        raise AssertionError(f"unexpected dependency use: {name}")


def _service(store: FakeStore) -> ProductRuntimeService:
    return ProductRuntimeService(
        catalog=NotUsed(),  # type: ignore[arg-type]
        evidence_store=store,  # type: ignore[arg-type]
        dag_store=NotUsed(),  # type: ignore[arg-type]
        provisioner=NotUsed(),  # type: ignore[arg-type]
        workspace_resolver=NotUsed(),  # type: ignore[arg-type]
        dispatcher=NotUsed(),  # type: ignore[arg-type]
    )


def test_service_rejects_partial_metrics_when_event_scan_bound_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "MAX_METRIC_EVENT_SCAN", 2)
    monkeypatch.setattr(service_module, "METRIC_EVENT_PAGE_SIZE", 2)
    store = FakeStore(_snapshot(), (_event(1), _event(2), _event(3)))

    with pytest.raises(ProductMetricsUnavailableError, match="scan limit"):
        asyncio.run(_service(store).get_run_metrics(RUN_ID))


class FakeApiService:
    async def get_run_metrics(self, run_id: UUID) -> ProductRunMetrics:
        assert run_id == RUN_ID
        return build_run_metrics(
            _snapshot(),
            aggregate_runtime_events(RUN_ID, (_event(1),)),
        )


async def _api_request(method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(
        app=create_app(FakeApiService()),  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path)


def test_metrics_api_is_get_only_and_rejects_browser_selectors() -> None:
    response = asyncio.run(_api_request("GET", f"/api/v1/runs/{RUN_ID}/metrics"))
    assert response.status_code == 200
    assert response.json()["status_basis"] == "PERSISTED_RUN"
    assert response.json()["evidence"]["verification_attempts"] == 3
    assert response.json()["evidence"]["reviewer_rejections"] == 1
    assert response.json()["evidence"]["scope_violations"] == 1

    injected = asyncio.run(
        _api_request("GET", f"/api/v1/runs/{RUN_ID}/metrics?success_rate=1")
    )
    assert injected.status_code == 400
    assert "does not accept browser-authored selectors" in injected.json()["detail"]

    write_attempt = asyncio.run(_api_request("POST", f"/api/v1/runs/{RUN_ID}/metrics"))
    assert write_attempt.status_code == 405
