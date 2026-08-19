from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.models.task import TaskContract
from app.persistence.dag import PersistedDAGSource
from app.persistence.types import PersistedRunStatus, PersistenceEvidenceKind


class ProductModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductProject(ProductModel):
    project_id: UUID
    repository_url: str
    default_branch: str
    created_at: datetime
    run_count: int = Field(ge=0)
    workspace_ready: bool


class ProductRun(ProductModel):
    run_id: UUID
    project_id: UUID
    status: PersistedRunStatus
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    task_count: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None = None


class ProductTaskSummary(ProductModel):
    task_id: str
    objective: str
    evidence_count: int = Field(ge=0)


class ProductRunDetail(ProductRun):
    repository_url: str
    default_branch: str
    tasks: tuple[ProductTaskSummary, ...]


class ProductRunStatusBasis(StrEnum):
    PERSISTED_RUN = "PERSISTED_RUN"


class ProductEvidenceMetrics(ProductModel):
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


class ProductRuntimeEventMetrics(ProductModel):
    total_events: int = Field(ge=0)
    warning_events: int = Field(ge=0)
    error_events: int = Field(ge=0)
    lease_acquisitions: int = Field(ge=0)
    lease_takeovers: int = Field(ge=0)
    lease_releases: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)


class ProductRunMetrics(ProductModel):
    run_id: UUID
    project_id: UUID
    status: PersistedRunStatus
    status_basis: ProductRunStatusBasis = ProductRunStatusBasis.PERSISTED_RUN
    task_count: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    terminal_duration_ms: int | None = Field(default=None, ge=0)
    evidence: ProductEvidenceMetrics
    runtime_events: ProductRuntimeEventMetrics


class ProductDAGNodeState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    REVIEWING = "REVIEWING"
    REPAIRING = "REPAIRING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class ProductDAGStateBasis(StrEnum):
    EVIDENCE = "EVIDENCE"
    DERIVED_DAG = "DERIVED_DAG"


class ProductDAGNode(ProductModel):
    task_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1)
    depends_on: tuple[str, ...] = ()
    topological_index: int = Field(ge=0)
    layer: int = Field(ge=0)
    presentation_state: ProductDAGNodeState
    state_basis: ProductDAGStateBasis


class ProductDAGEdge(ProductModel):
    source_task_id: str = Field(min_length=1, max_length=128)
    target_task_id: str = Field(min_length=1, max_length=128)


class ProductRunDAG(ProductModel):
    run_id: UUID
    dag_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    topology_source: PersistedDAGSource
    topological_order: tuple[str, ...]
    nodes: tuple[ProductDAGNode, ...]
    edges: tuple[ProductDAGEdge, ...]


class ProductDiffKind(StrEnum):
    TASK = "TASK"
    INTEGRATION = "INTEGRATION"


class ProductDiffEvidenceBasis(StrEnum):
    WORKER_EXECUTION = "WORKER_EXECUTION"
    MERGE_QUEUE_SNAPSHOT = "MERGE_QUEUE_SNAPSHOT"


class ProductDiffFileStatus(StrEnum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    TYPE_CHANGED = "TYPE_CHANGED"


class ProductDiffOmissionReason(StrEnum):
    BINARY = "BINARY"
    BLOB_LIMIT = "BLOB_LIMIT"
    TOTAL_PATCH_LIMIT = "TOTAL_PATCH_LIMIT"


class ProductDiffFile(ProductModel):
    path: str = Field(min_length=1)
    status: ProductDiffFileStatus
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)
    binary: bool
    patch: str | None = None
    patch_bytes: int = Field(ge=0)
    patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    patch_truncated: bool
    patch_omitted_reason: ProductDiffOmissionReason | None = None


class ProductTaskDiff(ProductModel):
    run_id: UUID
    project_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    diff_kind: ProductDiffKind
    evidence_basis: ProductDiffEvidenceBasis
    source_evidence_id: int = Field(ge=1)
    source_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_file_count: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    files: tuple[ProductDiffFile, ...]
    omitted_file_count: int = Field(ge=0)
    patch_bytes: int = Field(ge=0)
    truncated: bool


class ProductEvidenceSummary(ProductModel):
    evidence_id: int = Field(ge=1)
    kind: PersistenceEvidenceKind
    stage: str | None = None
    sequence: int | None = Field(default=None, ge=0)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime


class ProductTaskDetail(ProductModel):
    run_id: UUID
    project_id: UUID
    run_status: PersistedRunStatus
    task: TaskContract
    contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    evidence: tuple[ProductEvidenceSummary, ...]


class ProjectCreateRequest(ProductModel):
    repository_url: HttpUrl
    default_branch: str = Field(default="main", min_length=1, max_length=255)


class RunCreateRequest(ProductModel):
    project_id: UUID
    task: TaskContract


class DispatchStatus(StrEnum):
    QUEUED = "QUEUED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"


class RunLaunchResponse(ProductModel):
    run_id: UUID
    project_id: UUID
    task_id: str
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    dispatch_status: DispatchStatus
    dispatch_id: UUID | None = None
    broker_message_id: str | None = None
    queue_name: str | None = None
    detail: str | None = None
