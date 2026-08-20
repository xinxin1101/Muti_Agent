from __future__ import annotations

from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.api.models import DispatchStatus
from app.models.task import TaskContract
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


class BenchmarkModel(BaseModel):
    """Immutable benchmark schema that cannot silently accept extra authority fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkChangedFilesMode(StrEnum):
    EXACT = "EXACT"
    SUBSET = "SUBSET"


class BenchmarkObservationState(StrEnum):
    TERMINAL = "TERMINAL"
    FIXTURE_DRIFT = "FIXTURE_DRIFT"
    DISPATCH_UNAVAILABLE = "DISPATCH_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    API_ERROR = "API_ERROR"


class BenchmarkDimensionStatus(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    NOT_EVALUATED = "NOT_EVALUATED"


class BenchmarkCaseVerdict(StrEnum):
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    NOT_EVALUATED = "NOT_EVALUATED"


class BenchmarkDataAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_AVAILABLE = "NOT_AVAILABLE"


def _validate_github_https_url(value: HttpUrl) -> HttpUrl:
    parsed = urlparse(str(value))
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("benchmark repository URL contains an invalid port") from exc
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != "github.com"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "benchmark repositories must use credential-free https://github.com/owner/repo URLs"
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        raise ValueError("benchmark repository URL must identify exactly one owner/repository")
    if parts[1].endswith(".git"):
        parts[1] = parts[1][:-4]
    if not parts[0] or not parts[1]:
        raise ValueError("benchmark repository identity must not be empty")
    return value


def _validate_api_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("benchmark API base URL contains an invalid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.path not in {"", "/"})
    ):
        raise ValueError(
            "benchmark API base URL must be an origin without credentials or selectors"
        )
    if parsed.scheme == "http" and (parsed.hostname or "").lower() not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("plaintext benchmark API access is restricted to loopback hosts")
    if port is not None and not (1 <= port <= 65535):
        raise ValueError("benchmark API base URL port is invalid")
    return normalized


class BenchmarkExpectations(BenchmarkModel):
    terminal_status: PersistedRunStatus
    required_evidence_kinds: tuple[PersistenceEvidenceKind, ...] = ()
    changed_files: tuple[str, ...] = Field(min_length=1)
    changed_files_mode: BenchmarkChangedFilesMode = BenchmarkChangedFilesMode.EXACT
    max_terminal_duration_ms: int | None = Field(default=None, ge=0)
    max_repair_attempts: int | None = Field(default=None, ge=0)
    max_human_decisions: int | None = Field(default=None, ge=0)

    @field_validator("required_evidence_kinds")
    @classmethod
    def validate_unique_evidence_kinds(
        cls,
        values: tuple[PersistenceEvidenceKind, ...],
    ) -> tuple[PersistenceEvidenceKind, ...]:
        if len(values) != len(set(values)):
            raise ValueError("required_evidence_kinds must not contain duplicates")
        return values

    @field_validator("changed_files")
    @classmethod
    def validate_changed_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("benchmark changed-file expectations must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("benchmark changed-file expectations must not contain duplicates")
        if any(item.startswith(("/", "\\")) or "\\" in item for item in normalized):
            raise ValueError(
                "benchmark changed-file expectations must be repository-relative POSIX paths"
            )
        if any(any(part == ".." for part in item.split("/")) for item in normalized):
            raise ValueError("benchmark changed-file expectations must not traverse repositories")
        return normalized

    @model_validator(mode="after")
    def validate_terminal_expectation(self) -> BenchmarkExpectations:
        if self.terminal_status is PersistedRunStatus.RUNNING:
            raise ValueError("benchmark ground truth must expect a terminal Run status")
        return self


class BenchmarkCase(BenchmarkModel):
    case_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    description: str = Field(min_length=1, max_length=1000)
    repository_url: HttpUrl
    default_branch: str = Field(min_length=1, max_length=255)
    expected_base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    task: TaskContract
    expectations: BenchmarkExpectations
    tags: tuple[str, ...] = ()

    @field_validator("repository_url")
    @classmethod
    def validate_repository_url(cls, value: HttpUrl) -> HttpUrl:
        return _validate_github_https_url(value)

    @field_validator("default_branch")
    @classmethod
    def validate_default_branch(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or normalized.startswith(("/", "."))
            or normalized.endswith(("/", "."))
            or ".." in normalized
            or "@{" in normalized
            or "\\" in normalized
            or " " in normalized
        ):
            raise ValueError("benchmark default_branch must be a bounded Git branch name")
        return normalized

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in values)
        if any(not item for item in normalized):
            raise ValueError("benchmark tags must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("benchmark tags must not contain duplicates")
        return normalized


class BenchmarkSuite(BenchmarkModel):
    schema_version: Literal[1] = 1
    suite_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    suite_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    description: str = Field(min_length=1, max_length=4000)
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_case_identity(self) -> BenchmarkSuite:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark suite case_id values must be unique")
        return self


class BenchmarkFailure(BenchmarkModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9_]+$")
    message: str = Field(min_length=1, max_length=512)


class BenchmarkEvidenceObservation(BenchmarkModel):
    total_records: int = Field(ge=0)
    developer_runs: int = Field(ge=0)
    verification_attempts: int = Field(ge=0)
    review_decisions: int = Field(ge=0)
    repair_attempts: int = Field(ge=0)
    failure_reports: int = Field(ge=0)
    dispatch_events: int = Field(ge=0)
    worker_executions: int = Field(ge=0)
    merge_queue_snapshots: int = Field(ge=0)
    merge_conflicts: int = Field(ge=0)
    integration_gate_evaluations: int = Field(ge=0)
    human_decisions: int = Field(ge=0)


class BenchmarkRuntimeEventObservation(BenchmarkModel):
    total_events: int = Field(ge=0)
    warning_events: int = Field(ge=0)
    error_events: int = Field(ge=0)
    lease_acquisitions: int = Field(ge=0)
    lease_takeovers: int = Field(ge=0)
    lease_releases: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)


class BenchmarkDiffObservation(BenchmarkModel):
    source_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_file_count: int = Field(ge=0)
    changed_files: tuple[str, ...]
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    truncated: bool
    omitted_file_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_file_count(self) -> BenchmarkDiffObservation:
        if not self.truncated and self.omitted_file_count == 0:
            if self.changed_file_count != len(self.changed_files):
                raise ValueError("complete benchmark diff observations require exact file counts")
        if len(self.changed_files) != len(set(self.changed_files)):
            raise ValueError("benchmark diff observations must not repeat changed files")
        return self

    @property
    def complete(self) -> bool:
        return not self.truncated and self.omitted_file_count == 0


class BenchmarkObservation(BenchmarkModel):
    schema_version: Literal[1] = 1
    suite_id: str = Field(min_length=1, max_length=128)
    suite_version: str = Field(min_length=1, max_length=32)
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_id: str = Field(min_length=1, max_length=128)
    state: BenchmarkObservationState
    project_id: UUID | None = None
    run_id: UUID | None = None
    dispatch_status: DispatchStatus | None = None
    observed_base_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    run_status: PersistedRunStatus | None = None
    terminal_duration_ms: int | None = Field(default=None, ge=0)
    evidence_kinds: tuple[PersistenceEvidenceKind, ...] = ()
    evidence: BenchmarkEvidenceObservation | None = None
    runtime_events: BenchmarkRuntimeEventObservation | None = None
    diff: BenchmarkDiffObservation | None = None
    failure: BenchmarkFailure | None = None

    @model_validator(mode="after")
    def validate_state_shape(self) -> BenchmarkObservation:
        if len(self.evidence_kinds) != len(set(self.evidence_kinds)):
            raise ValueError("benchmark observation evidence kinds must be unique")
        if self.state is BenchmarkObservationState.TERMINAL:
            if self.project_id is None or self.run_id is None or self.observed_base_commit is None:
                raise ValueError(
                    "terminal benchmark observations require project/run/base identities"
                )
            if self.run_status not in {PersistedRunStatus.SUCCEEDED, PersistedRunStatus.FAILED}:
                raise ValueError(
                    "terminal benchmark observations require a terminal persisted Run status"
                )
            if (
                self.terminal_duration_ms is None
                or self.evidence is None
                or self.runtime_events is None
            ):
                raise ValueError("terminal benchmark observations require persisted Metrics facts")
            if self.failure is not None:
                raise ValueError(
                    "terminal benchmark observations must not carry transport failures"
                )
        else:
            if self.failure is None:
                raise ValueError(
                    "non-terminal benchmark observations require a bounded failure reason"
                )
            if self.run_status is PersistedRunStatus.RUNNING:
                raise ValueError(
                    "non-terminal benchmark observations must not promote RUNNING to a result"
                )
        return self


class BenchmarkExecutionConfig(BenchmarkModel):
    api_base_url: str = "http://127.0.0.1:8000"
    poll_interval_seconds: float = Field(default=1.0, ge=0.05, le=60.0)
    timeout_per_case_seconds: float = Field(default=900.0, ge=1.0, le=7200.0)

    @field_validator("api_base_url")
    @classmethod
    def validate_api_base_url(cls, value: str) -> str:
        return _validate_api_base_url(value)


class BenchmarkObservationBundle(BenchmarkModel):
    schema_version: Literal[1] = 1
    suite_id: str = Field(min_length=1, max_length=128)
    suite_version: str = Field(min_length=1, max_length=32)
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution: BenchmarkExecutionConfig
    observations: tuple[BenchmarkObservation, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_observation_identity(self) -> BenchmarkObservationBundle:
        case_ids = [item.case_id for item in self.observations]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark observation bundle case_id values must be unique")
        for item in self.observations:
            if (
                item.suite_id != self.suite_id
                or item.suite_version != self.suite_version
                or item.suite_sha256 != self.suite_sha256
            ):
                raise ValueError("benchmark observation identity must match its bundle")
        return self


class BenchmarkDimensionResult(BenchmarkModel):
    status: BenchmarkDimensionStatus
    expected: str = Field(min_length=1, max_length=2000)
    observed: str = Field(min_length=1, max_length=2000)


class BenchmarkCaseEvaluation(BenchmarkModel):
    case_id: str = Field(min_length=1, max_length=128)
    runtime_status: PersistedRunStatus | None
    observation_state: BenchmarkObservationState
    verdict: BenchmarkCaseVerdict
    completion: BenchmarkDimensionResult
    evidence: BenchmarkDimensionResult
    code_delta: BenchmarkDimensionResult
    reliability: BenchmarkDimensionResult
    latency: BenchmarkDimensionResult
    cost_data: BenchmarkDataAvailability = BenchmarkDataAvailability.NOT_AVAILABLE
    failure_modes: tuple[str, ...] = ()


class BenchmarkSummary(BenchmarkModel):
    total_cases: int = Field(ge=1)
    matched_cases: int = Field(ge=0)
    mismatched_cases: int = Field(ge=0)
    not_evaluated_cases: int = Field(ge=0)
    completion_matches: int = Field(ge=0)
    evidence_matches: int = Field(ge=0)
    code_delta_matches: int = Field(ge=0)
    reliability_matches: int = Field(ge=0)
    latency_matches: int = Field(ge=0)
    cost_data: BenchmarkDataAvailability = BenchmarkDataAvailability.NOT_AVAILABLE

    @model_validator(mode="after")
    def validate_case_totals(self) -> BenchmarkSummary:
        if (
            self.matched_cases
            + self.mismatched_cases
            + self.not_evaluated_cases
            != self.total_cases
        ):
            raise ValueError("benchmark summary verdict counts must equal total_cases")
        return self


class BenchmarkReport(BenchmarkModel):
    schema_version: Literal[1] = 1
    suite_id: str
    suite_version: str
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution: BenchmarkExecutionConfig
    cases: tuple[BenchmarkCaseEvaluation, ...]
    summary: BenchmarkSummary
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
