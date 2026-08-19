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
