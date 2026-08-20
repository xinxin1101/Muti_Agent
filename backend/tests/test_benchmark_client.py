from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import UTC, datetime

import httpx

from app.benchmark.client import BenchmarkApiClient
from app.benchmark.io import canonical_sha256
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkChangedFilesMode,
    BenchmarkExecutionConfig,
    BenchmarkExpectations,
    BenchmarkObservationState,
    BenchmarkSuite,
)
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
DISPATCH_ID = "33333333-3333-3333-3333-333333333333"
BASE = "a" * 40
HEAD = "b" * 40
NOW = datetime(2026, 8, 20, tzinfo=UTC).isoformat()


def _suite() -> tuple[BenchmarkSuite, str]:
    case = BenchmarkCase(
        case_id="case",
        description="case",
        repository_url="https://github.com/example/repo",
        default_branch="benchmark-v1",
        expected_base_commit=BASE,
        task=TaskContract(
            task_id="task",
            objective="Create result.txt.",
            readable_files=[],
            writable_files=["result.txt"],
            readonly_files=[],
            acceptance_criteria=["result.txt exists."],
            verification_commands=[
                "python -c \"from pathlib import Path; "
                "assert Path('result.txt').exists()\""
            ],
            max_retries=1,
        ),
        expectations=BenchmarkExpectations(
            terminal_status=PersistedRunStatus.SUCCEEDED,
            required_evidence_kinds=(
                PersistenceEvidenceKind.VERIFICATION_RESULT,
                PersistenceEvidenceKind.REVIEW_DECISION,
                PersistenceEvidenceKind.WORKER_EXECUTION,
            ),
            changed_files=("result.txt",),
            changed_files_mode=BenchmarkChangedFilesMode.EXACT,
            max_terminal_duration_ms=5000,
            max_repair_attempts=1,
            max_human_decisions=0,
        ),
    )
    suite = BenchmarkSuite(
        suite_id="suite",
        suite_version="1.0.0",
        description="suite",
        cases=(case,),
    )
    return suite, canonical_sha256(suite)


def _project_response() -> dict[str, object]:
    return {
        "project_id": PROJECT_ID,
        "repository_url": "https://github.com/example/repo",
        "default_branch": "benchmark-v1",
        "created_at": NOW,
        "run_count": 0,
        "workspace_ready": True,
    }


def _run_response(status: str = "SUCCEEDED") -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "repository_url": "https://github.com/example/repo",
        "default_branch": "benchmark-v1",
        "status": status,
        "base_commit": BASE,
        "task_count": 1,
        "started_at": NOW,
        "finished_at": NOW if status != "RUNNING" else None,
        "tasks": [
            {
                "task_id": "task",
                "objective": "Create result.txt.",
                "evidence_count": 3,
            }
        ],
    }


def _metrics_response() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "status": "SUCCEEDED",
        "status_basis": "PERSISTED_RUN",
        "task_count": 1,
        "started_at": NOW,
        "finished_at": NOW,
        "terminal_duration_ms": 2000,
        "evidence": {
            "total_records": 7,
            "developer_runs": 1,
            "verification_attempts": 1,
            "review_decisions": 1,
            "repair_attempts": 0,
            "failure_reports": 0,
            "dispatch_events": 1,
            "worker_executions": 1,
            "merge_queue_snapshots": 0,
            "merge_conflicts": 0,
            "integration_gate_evaluations": 0,
            "human_decisions": 0,
        },
        "runtime_events": {
            "total_events": 8,
            "warning_events": 0,
            "error_events": 0,
            "lease_acquisitions": 1,
            "lease_takeovers": 0,
            "lease_releases": 1,
            "latest_sequence": 8,
        },
    }


def _task_response(task: TaskContract) -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "run_status": "SUCCEEDED",
        "task": task.model_dump(mode="json"),
        "contract_sha256": "c" * 64,
        "created_at": NOW,
        "evidence": [
            {
                "evidence_id": 1,
                "kind": "VERIFICATION_RESULT",
                "stage": "VERIFY",
                "sequence": 1,
                "payload_sha256": "d" * 64,
                "created_at": NOW,
            },
            {
                "evidence_id": 2,
                "kind": "REVIEW_DECISION",
                "stage": "REVIEW",
                "sequence": 2,
                "payload_sha256": "e" * 64,
                "created_at": NOW,
            },
            {
                "evidence_id": 3,
                "kind": "WORKER_EXECUTION",
                "stage": "WORKER",
                "sequence": 3,
                "payload_sha256": "f" * 64,
                "created_at": NOW,
            },
        ],
    }


def _diff_response() -> dict[str, object]:
    return {
        "run_id": RUN_ID,
        "project_id": PROJECT_ID,
        "task_id": "task",
        "diff_kind": "TASK",
        "evidence_basis": "WORKER_EXECUTION",
        "source_evidence_id": 3,
        "source_evidence_sha256": "f" * 64,
        "base_commit": BASE,
        "head_commit": HEAD,
        "changed_file_count": 1,
        "additions": 1,
        "deletions": 0,
        "files": [
            {
                "path": "result.txt",
                "status": "ADDED",
                "additions": 1,
                "deletions": 0,
                "binary": False,
                "patch": "@@ -0,0 +1 @@\\n+ok",
                "patch_bytes": 18,
                "patch_sha256": "1" * 64,
                "patch_truncated": False,
                "patch_omitted_reason": None,
            }
        ],
        "omitted_file_count": 0,
        "patch_bytes": 18,
        "truncated": False,
    }


def test_live_runner_uses_existing_product_api_without_browser_authored_sha() -> None:
    suite, suite_sha = _suite()
    requests: list[tuple[str, str, dict[str, object] | None]] = []
    counts: Counter[str] = Counter()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        requests.append((request.method, request.url.path, body))
        counts[request.url.path] += 1
        if request.method == "POST" and request.url.path == "/api/v1/projects":
            return httpx.Response(201, json=_project_response())
        if request.method == "POST" and request.url.path == "/api/v1/runs":
            return httpx.Response(
                201,
                json={
                    "run_id": RUN_ID,
                    "project_id": PROJECT_ID,
                    "task_id": "task",
                    "base_commit": BASE,
                    "dispatch_status": "QUEUED",
                    "dispatch_id": DISPATCH_ID,
                    "broker_message_id": "message",
                    "queue_name": "devflow_tasks",
                    "detail": None,
                },
            )
        if request.url.path == f"/api/v1/runs/{RUN_ID}":
            return httpx.Response(200, json=_run_response())
        if request.url.path == f"/api/v1/runs/{RUN_ID}/metrics":
            return httpx.Response(200, json=_metrics_response())
        if request.url.path == f"/api/v1/runs/{RUN_ID}/tasks/task":
            return httpx.Response(200, json=_task_response(suite.cases[0].task))
        if request.url.path == f"/api/v1/runs/{RUN_ID}/tasks/task/diff":
            assert dict(request.url.params) == {"kind": "TASK"}
            return httpx.Response(200, json=_diff_response())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def scenario():
        async_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handler),
        )
        client = BenchmarkApiClient(
            BenchmarkExecutionConfig(
                api_base_url="http://127.0.0.1:8000",
                poll_interval_seconds=0.05,
                timeout_per_case_seconds=2,
            ),
            client=async_client,
        )
        try:
            return await client.run_suite(suite, suite_sha)
        finally:
            await async_client.aclose()

    bundle = asyncio.run(scenario())

    observation = bundle.observations[0]
    assert observation.state is BenchmarkObservationState.TERMINAL
    assert observation.run_status is PersistedRunStatus.SUCCEEDED
    assert observation.diff is not None
    assert observation.diff.changed_files == ("result.txt",)

    run_posts = [
        body
        for method, path, body in requests
        if method == "POST" and path == "/api/v1/runs"
    ]
    assert len(run_posts) == 1
    assert set(run_posts[0]) == {"project_id", "task"}
    assert "base_commit" not in run_posts[0]
    assert "expected_base_commit" not in run_posts[0]
    serialized = json.dumps(run_posts[0])
    assert "token" not in serialized.lower()
    assert counts[f"/api/v1/runs/{RUN_ID}/metrics"] == 1


def test_fixture_drift_stops_benchmark_evaluation_after_backend_selects_base() -> None:
    suite, suite_sha = _suite()
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/v1/projects":
            return httpx.Response(201, json=_project_response())
        if request.url.path == "/api/v1/runs":
            return httpx.Response(
                201,
                json={
                    "run_id": RUN_ID,
                    "project_id": PROJECT_ID,
                    "task_id": "task",
                    "base_commit": "9" * 40,
                    "dispatch_status": "QUEUED",
                    "dispatch_id": DISPATCH_ID,
                    "broker_message_id": "message",
                    "queue_name": "devflow_tasks",
                    "detail": None,
                },
            )
        raise AssertionError("fixture drift must stop benchmark polling")

    async def scenario():
        async_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handler),
        )
        client = BenchmarkApiClient(BenchmarkExecutionConfig(), client=async_client)
        try:
            return await client.run_case(
                suite=suite,
                suite_sha256=suite_sha,
                case=suite.cases[0],
            )
        finally:
            await async_client.aclose()

    observation = asyncio.run(scenario())

    assert observation.state is BenchmarkObservationState.FIXTURE_DRIFT
    assert observation.observed_base_commit == "9" * 40
    assert requested_paths == ["/api/v1/projects", "/api/v1/runs"]


def test_broker_failure_becomes_observation_without_changing_runtime_truth() -> None:
    suite, suite_sha = _suite()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/projects":
            return httpx.Response(201, json=_project_response())
        if request.url.path == "/api/v1/runs":
            return httpx.Response(
                201,
                json={
                    "run_id": RUN_ID,
                    "project_id": PROJECT_ID,
                    "task_id": "task",
                    "base_commit": BASE,
                    "dispatch_status": "BROKER_UNAVAILABLE",
                    "dispatch_id": None,
                    "broker_message_id": None,
                    "queue_name": None,
                    "detail": "broker unavailable",
                },
            )
        raise AssertionError("broker failure must stop benchmark polling")

    async def scenario():
        async_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handler),
        )
        client = BenchmarkApiClient(BenchmarkExecutionConfig(), client=async_client)
        try:
            return await client.run_case(
                suite=suite,
                suite_sha256=suite_sha,
                case=suite.cases[0],
            )
        finally:
            await async_client.aclose()

    observation = asyncio.run(scenario())

    assert observation.state is BenchmarkObservationState.DISPATCH_UNAVAILABLE
    assert observation.run_status is None
    assert observation.failure is not None
    assert observation.failure.code == "DISPATCH_UNAVAILABLE"


def test_api_error_persists_only_bounded_public_detail() -> None:
    suite, suite_sha = _suite()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "x" * 5000})

    async def scenario():
        async_client = httpx.AsyncClient(
            base_url="http://127.0.0.1:8000",
            transport=httpx.MockTransport(handler),
        )
        client = BenchmarkApiClient(BenchmarkExecutionConfig(), client=async_client)
        try:
            return await client.run_case(
                suite=suite,
                suite_sha256=suite_sha,
                case=suite.cases[0],
            )
        finally:
            await async_client.aclose()

    observation = asyncio.run(scenario())

    assert observation.state is BenchmarkObservationState.API_ERROR
    assert observation.failure is not None
    assert observation.failure.code == "API_HTTP_502"
    assert len(observation.failure.message) <= 512
