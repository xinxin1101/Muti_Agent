from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import TypeVar
from uuid import UUID

import httpx
from pydantic import BaseModel, ValidationError

from app.api.models import (
    DispatchStatus,
    ProductDiffKind,
    ProductProject,
    ProductRunDetail,
    ProductRunMetrics,
    ProductTaskDetail,
    ProductTaskDiff,
    ProjectCreateRequest,
    RunCreateRequest,
    RunLaunchResponse,
)
from app.benchmark.models import (
    BenchmarkCase,
    BenchmarkDiffObservation,
    BenchmarkEvidenceObservation,
    BenchmarkExecutionConfig,
    BenchmarkFailure,
    BenchmarkObservation,
    BenchmarkObservationBundle,
    BenchmarkObservationState,
    BenchmarkRuntimeEventObservation,
    BenchmarkSuite,
)
from app.persistence.types import PersistedRunStatus

_RESPONSE_LIMIT_BYTES = 1024 * 1024
T = TypeVar("T", bound=BaseModel)


class BenchmarkApiError(RuntimeError):
    """Bounded product-API failure safe to record in benchmark observations."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message[:512]


class BenchmarkApiClient:
    """External benchmark client that uses only accepted Product API boundaries."""

    def __init__(
        self,
        execution: BenchmarkExecutionConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.execution = execution
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=execution.api_base_url,
            timeout=min(execution.timeout_per_case_seconds, 60.0),
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> BenchmarkApiClient:
        return self

    async def __aexit__(self, *_args) -> None:
        await self.close()

    async def create_project(self, case: BenchmarkCase) -> ProductProject:
        request = ProjectCreateRequest(
            repository_url=case.repository_url,
            default_branch=case.default_branch,
        )
        return await self._request_model(
            "POST",
            "/api/v1/projects",
            ProductProject,
            expected_statuses={201},
            json=request.model_dump(mode="json"),
        )

    async def create_run(self, project_id: UUID, case: BenchmarkCase) -> RunLaunchResponse:
        request = RunCreateRequest(project_id=project_id, task=case.task)
        return await self._request_model(
            "POST",
            "/api/v1/runs",
            RunLaunchResponse,
            expected_statuses={201},
            json=request.model_dump(mode="json"),
        )

    async def get_run(self, run_id: UUID) -> ProductRunDetail:
        return await self._request_model(
            "GET",
            f"/api/v1/runs/{run_id}",
            ProductRunDetail,
            expected_statuses={200},
        )

    async def get_metrics(self, run_id: UUID) -> ProductRunMetrics:
        return await self._request_model(
            "GET",
            f"/api/v1/runs/{run_id}/metrics",
            ProductRunMetrics,
            expected_statuses={200},
        )

    async def get_task(self, run_id: UUID, task_id: str) -> ProductTaskDetail:
        return await self._request_model(
            "GET",
            f"/api/v1/runs/{run_id}/tasks/{task_id}",
            ProductTaskDetail,
            expected_statuses={200},
        )

    async def get_task_diff(self, run_id: UUID, task_id: str) -> ProductTaskDiff | None:
        response = await self._request(
            "GET",
            f"/api/v1/runs/{run_id}/tasks/{task_id}/diff",
            expected_statuses={200, 409},
            params={"kind": ProductDiffKind.TASK.value},
        )
        if response.status_code == 409:
            return None
        try:
            return ProductTaskDiff.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise BenchmarkApiError(
                "API_RESPONSE_INVALID",
                "DevFlow Diff API returned an invalid typed response.",
            ) from exc

    async def run_case(
        self,
        *,
        suite: BenchmarkSuite,
        suite_sha256: str,
        case: BenchmarkCase,
    ) -> BenchmarkObservation:
        project_id: UUID | None = None
        run_id: UUID | None = None
        dispatch_status: DispatchStatus | None = None
        observed_base_commit: str | None = None
        terminal_status: PersistedRunStatus | None = None
        try:
            project = await self.create_project(case)
            if not project.workspace_ready:
                raise BenchmarkApiError(
                    "WORKSPACE_NOT_READY",
                    "DevFlow project workspace is not ready after project provisioning.",
                )
            project_id = project.project_id

            launch = await self.create_run(project.project_id, case)
            run_id = launch.run_id
            dispatch_status = launch.dispatch_status
            observed_base_commit = launch.base_commit

            if launch.base_commit != case.expected_base_commit:
                return self._non_terminal_observation(
                    suite=suite,
                    suite_sha256=suite_sha256,
                    case=case,
                    state=BenchmarkObservationState.FIXTURE_DRIFT,
                    project_id=project_id,
                    run_id=run_id,
                    dispatch_status=dispatch_status,
                    observed_base_commit=observed_base_commit,
                    code="FIXTURE_DRIFT",
                    message=(
                        "Backend-selected Run base does not match the versioned benchmark fixture; "
                        "the case is not evaluated."
                    ),
                )

            if launch.dispatch_status is DispatchStatus.BROKER_UNAVAILABLE:
                return self._non_terminal_observation(
                    suite=suite,
                    suite_sha256=suite_sha256,
                    case=case,
                    state=BenchmarkObservationState.DISPATCH_UNAVAILABLE,
                    project_id=project_id,
                    run_id=run_id,
                    dispatch_status=dispatch_status,
                    observed_base_commit=observed_base_commit,
                    code="DISPATCH_UNAVAILABLE",
                    message=launch.detail or "DevFlow broker rejected benchmark task dispatch.",
                )

            deadline = time.monotonic() + self.execution.timeout_per_case_seconds
            while True:
                current = await self.get_run(run_id)
                if current.base_commit != case.expected_base_commit:
                    return self._non_terminal_observation(
                        suite=suite,
                        suite_sha256=suite_sha256,
                        case=case,
                        state=BenchmarkObservationState.FIXTURE_DRIFT,
                        project_id=project_id,
                        run_id=run_id,
                        dispatch_status=dispatch_status,
                        observed_base_commit=current.base_commit,
                        code="RUN_BASE_CHANGED",
                        message=(
                            "Persisted Run base no longer matches the versioned fixture identity."
                        ),
                    )
                if current.status in {PersistedRunStatus.SUCCEEDED, PersistedRunStatus.FAILED}:
                    terminal_status = current.status
                    break
                if time.monotonic() >= deadline:
                    return self._non_terminal_observation(
                        suite=suite,
                        suite_sha256=suite_sha256,
                        case=case,
                        state=BenchmarkObservationState.TIMEOUT,
                        project_id=project_id,
                        run_id=run_id,
                        dispatch_status=dispatch_status,
                        observed_base_commit=observed_base_commit,
                        code="BENCHMARK_TIMEOUT",
                        message=(
                            "Run did not reach a persisted terminal state within the case timeout."
                        ),
                    )
                await asyncio.sleep(self.execution.poll_interval_seconds)

            metrics = await self.get_metrics(run_id)
            if metrics.status is not terminal_status:
                raise BenchmarkApiError(
                    "METRICS_STATUS_MISMATCH",
                    "Persisted Run and Metrics status projections disagree.",
                )
            task = await self.get_task(run_id, case.task.task_id)
            if task.run_status is not terminal_status:
                raise BenchmarkApiError(
                    "TASK_STATUS_MISMATCH",
                    "Persisted Run and Task Detail status projections disagree.",
                )

            diff = None
            if terminal_status is PersistedRunStatus.SUCCEEDED:
                product_diff = await self.get_task_diff(run_id, case.task.task_id)
                if product_diff is not None:
                    diff = BenchmarkDiffObservation(
                        source_evidence_sha256=product_diff.source_evidence_sha256,
                        base_commit=product_diff.base_commit,
                        head_commit=product_diff.head_commit,
                        changed_file_count=product_diff.changed_file_count,
                        changed_files=tuple(file.path for file in product_diff.files),
                        additions=product_diff.additions,
                        deletions=product_diff.deletions,
                        truncated=product_diff.truncated,
                        omitted_file_count=product_diff.omitted_file_count,
                    )

            evidence_kinds = tuple(
                sorted({item.kind for item in task.evidence}, key=lambda item: item.value)
            )
            return BenchmarkObservation(
                suite_id=suite.suite_id,
                suite_version=suite.suite_version,
                suite_sha256=suite_sha256,
                case_id=case.case_id,
                state=BenchmarkObservationState.TERMINAL,
                project_id=project_id,
                run_id=run_id,
                dispatch_status=dispatch_status,
                observed_base_commit=observed_base_commit,
                run_status=terminal_status,
                terminal_duration_ms=metrics.terminal_duration_ms,
                evidence_kinds=evidence_kinds,
                evidence=BenchmarkEvidenceObservation(**metrics.evidence.model_dump(mode="python")),
                runtime_events=BenchmarkRuntimeEventObservation(
                    **metrics.runtime_events.model_dump(mode="python")
                ),
                diff=diff,
            )
        except BenchmarkApiError as exc:
            return self._non_terminal_observation(
                suite=suite,
                suite_sha256=suite_sha256,
                case=case,
                state=BenchmarkObservationState.API_ERROR,
                project_id=project_id,
                run_id=run_id,
                dispatch_status=dispatch_status,
                observed_base_commit=observed_base_commit,
                run_status=terminal_status,
                code=exc.code,
                message=exc.public_message,
            )

    async def run_suite(
        self,
        suite: BenchmarkSuite,
        suite_sha256: str,
    ) -> BenchmarkObservationBundle:
        observations = []
        for case in suite.cases:
            observations.append(
                await self.run_case(
                    suite=suite,
                    suite_sha256=suite_sha256,
                    case=case,
                )
            )
        return BenchmarkObservationBundle(
            suite_id=suite.suite_id,
            suite_version=suite.suite_version,
            suite_sha256=suite_sha256,
            execution=self.execution,
            observations=tuple(observations),
        )

    async def _request_model(
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        expected_statuses: set[int],
        json: dict[str, object] | None = None,
    ) -> T:
        response = await self._request(
            method,
            path,
            expected_statuses=expected_statuses,
            json=json,
        )
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise BenchmarkApiError(
                "API_RESPONSE_INVALID",
                f"DevFlow {path} returned an invalid typed response.",
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected_statuses: Iterable[int],
        json: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json, params=params)
        except httpx.HTTPError as exc:
            raise BenchmarkApiError(
                "API_UNAVAILABLE",
                "DevFlow Product API request failed.",
            ) from exc
        if len(response.content) > _RESPONSE_LIMIT_BYTES:
            raise BenchmarkApiError(
                "API_RESPONSE_TOO_LARGE",
                "DevFlow Product API response exceeded the benchmark client bound.",
            )
        if response.status_code not in set(expected_statuses):
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
                    detail = payload["detail"].strip()[:400]
            except ValueError:
                pass
            suffix = f": {detail}" if detail else ""
            raise BenchmarkApiError(
                f"API_HTTP_{response.status_code}",
                f"DevFlow Product API rejected the benchmark request with HTTP "
                f"{response.status_code}{suffix}",
            )
        return response

    @staticmethod
    def _non_terminal_observation(
        *,
        suite: BenchmarkSuite,
        suite_sha256: str,
        case: BenchmarkCase,
        state: BenchmarkObservationState,
        project_id: UUID | None,
        run_id: UUID | None,
        dispatch_status: DispatchStatus | None,
        observed_base_commit: str | None,
        code: str,
        message: str,
        run_status: PersistedRunStatus | None = None,
    ) -> BenchmarkObservation:
        return BenchmarkObservation(
            suite_id=suite.suite_id,
            suite_version=suite.suite_version,
            suite_sha256=suite_sha256,
            case_id=case.case_id,
            state=state,
            project_id=project_id,
            run_id=run_id,
            dispatch_status=dispatch_status,
            observed_base_commit=observed_base_commit,
            run_status=run_status,
            failure=BenchmarkFailure(code=code, message=message[:512]),
        )
