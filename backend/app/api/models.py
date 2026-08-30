from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from app.models.checkpoint import CheckpointReason
from app.models.failure import FailureSource, FailureType
from app.models.project import ProjectProvisionStatus
from app.models.publication import GitHubPublicationSourceBasis, GitHubPublicationState
from app.models.task import TaskContract
from app.models.token_budget import RunTokenBudgetStatus
from app.models.workflow import WorkflowActivationMode, WorkflowExecutionMode, WorkflowId
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
    provision_status: ProjectProvisionStatus = ProjectProvisionStatus.READY
    provision_error_code: str | None = Field(default=None, max_length=64)
    provision_error_message: str | None = Field(default=None, max_length=512)


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


class ProductRunFailure(ProductModel):
    """Bounded user-facing projection of accepted terminal failure evidence."""

    task_id: str | None = Field(default=None, max_length=128)
    failure_type: FailureType
    source: FailureSource
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
    evidence: tuple[str, ...] = Field(default_factory=tuple, max_length=8)


class ProductRunCheckpoint(ProductModel):
    task_id: str = Field(min_length=1, max_length=128)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_files: tuple[str, ...] = Field(min_length=1, max_length=512)
    reason: CheckpointReason
    summary: str = Field(min_length=1, max_length=512)
    remaining_budget_tokens: int | None = Field(default=None, ge=0)


class ProductFailureExplanation(ProductModel):
    run_id: UUID
    failure_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    explanation: str = Field(min_length=1, max_length=2_000)
    model: str = Field(min_length=1, max_length=256)
    cached: bool
    created_at: datetime


class ProductRunDetail(ProductRun):
    repository_url: str
    default_branch: str
    tasks: tuple[ProductTaskSummary, ...]
    failures: tuple[ProductRunFailure, ...] = ()
    checkpoint: ProductRunCheckpoint | None = None


class ProductRunStatusBasis(StrEnum):
    PERSISTED_RUN = "PERSISTED_RUN"


class ProductEvidenceMetrics(ProductModel):
    total_records: int = Field(ge=0)
    developer_runs: int = Field(ge=0)
    verification_attempts: int = Field(ge=0)
    review_decisions: int = Field(ge=0)
    reviewer_rejections: int = Field(default=0, ge=0)
    repair_attempts: int = Field(ge=0)
    failure_reports: int = Field(ge=0)
    scope_violations: int = Field(default=0, ge=0)
    dispatch_events: int = Field(ge=0)
    worker_executions: int = Field(ge=0)
    merge_queue_snapshots: int = Field(ge=0)
    merge_conflicts: int = Field(ge=0)
    integration_gate_evaluations: int = Field(ge=0)
    human_decisions: int = Field(ge=0)
    developer_prompt_tokens: int = Field(default=0, ge=0)
    developer_completion_tokens: int = Field(default=0, ge=0)
    developer_total_tokens: int = Field(default=0, ge=0)
    repair_prompt_tokens: int = Field(default=0, ge=0)
    repair_completion_tokens: int = Field(default=0, ge=0)
    repair_total_tokens: int = Field(default=0, ge=0)
    reviewer_token_usage_available: bool = False
    estimated_cost_available: bool = False


class ProductRuntimeEventMetrics(ProductModel):
    total_events: int = Field(ge=0)
    warning_events: int = Field(ge=0)
    error_events: int = Field(ge=0)
    lease_acquisitions: int = Field(ge=0)
    lease_takeovers: int = Field(ge=0)
    lease_releases: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)


class ProductStagePerformanceMetrics(ProductModel):
    """Durable stage timings reduced from accepted trace and worker evidence."""

    developer_model_latency_ms: int = Field(default=0, ge=0)
    repair_model_latency_ms: int = Field(default=0, ge=0)
    repository_tool_latency_ms: int = Field(default=0, ge=0)
    verification_latency_ms: int = Field(default=0, ge=0)
    context_estimated_tokens: int = Field(default=0, ge=0)
    context_reused_files: int = Field(default=0, ge=0)
    context_trimmed_files: int = Field(default=0, ge=0)
    context_compacted_tool_groups: int = Field(default=0, ge=0)


class ProductRoleTokenUsage(ProductModel):
    role: str = Field(min_length=1, max_length=16)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    call_count: int = Field(ge=0)


class ProductStageTokenBudget(ProductModel):
    stage: str = Field(min_length=1, max_length=32)
    total_budget_tokens: int = Field(ge=0)
    used_tokens: int = Field(ge=0)
    reserved_tokens: int = Field(ge=0)


class ProductWorkPackageTokenBudget(ProductModel):
    task_id: str = Field(min_length=1, max_length=128)
    complexity: str = Field(pattern=r"^(LOW|MEDIUM|HIGH)$")
    total_budget_tokens: int = Field(ge=0)
    developer_budget_tokens: int = Field(ge=0)
    repair_budget_tokens: int = Field(ge=0)
    developer_used_tokens: int = Field(ge=0)
    repair_used_tokens: int = Field(ge=0)
    developer_reserved_tokens: int = Field(ge=0)
    repair_reserved_tokens: int = Field(ge=0)
    developer_borrowed_tokens: int = Field(default=0, ge=0)
    repair_borrowed_tokens: int = Field(default=0, ge=0)
    developer_reclaimed_tokens: int = Field(default=0, ge=0)
    repair_reclaimed_tokens: int = Field(default=0, ge=0)
    borrow_count: int = Field(default=0, ge=0)
    last_required_tokens: int = Field(default=0, ge=0)
    last_available_tokens: int = Field(default=0, ge=0)
    last_flex_available_tokens: int = Field(default=0, ge=0)
    last_borrowed_tokens: int = Field(default=0, ge=0)
    last_budget_decision: str | None = Field(default=None, max_length=64)
    status: str = Field(pattern=r"^(ACTIVE|RECLAIMED)$")


class ProductRunTokenBudget(ProductModel):
    total_budget_tokens: int = Field(default=30_000, ge=1)
    used_prompt_tokens: int = Field(default=0, ge=0)
    used_completion_tokens: int = Field(default=0, ge=0)
    used_total_tokens: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    status: RunTokenBudgetStatus = RunTokenBudgetStatus.NORMAL
    roles: tuple[ProductRoleTokenUsage, ...] = ()
    stages: tuple[ProductStageTokenBudget, ...] = ()
    work_packages: tuple[ProductWorkPackageTokenBudget, ...] = ()


class ProductPlanningTokenBudget(ProductModel):
    total_budget_tokens: int = Field(ge=1)
    used_total_tokens: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    enable_thinking: bool
    status: RunTokenBudgetStatus


class ProductWorkflowMetrics(ProductModel):
    activation_mode: WorkflowActivationMode
    workflow_tasks: int = Field(default=0, ge=0)
    agent_tasks: int = Field(default=0, ge=0)
    hybrid_tasks: int = Field(default=0, ge=0)
    workflow_calls: int = Field(default=0, ge=0)
    agent_calls: int = Field(default=0, ge=0)
    workflow_duration_ms: int = Field(default=0, ge=0)
    estimated_tokens_saved: int = Field(default=0, ge=0)
    agent_escalations: int = Field(default=0, ge=0)


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
    token_budget: ProductRunTokenBudget = Field(default_factory=ProductRunTokenBudget)
    planning_budget: ProductPlanningTokenBudget | None = None
    workflow: ProductWorkflowMetrics = Field(
        default_factory=lambda: ProductWorkflowMetrics(
            activation_mode=WorkflowActivationMode.WORKFLOW_FIRST
        )
    )
    performance: ProductStagePerformanceMetrics = Field(
        default_factory=ProductStagePerformanceMetrics
    )


class ProductGitHubPublication(ProductModel):
    run_id: UUID
    project_id: UUID
    state: GitHubPublicationState
    source_basis: GitHubPublicationSourceBasis
    source_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    source_evidence_id: int = Field(ge=1)
    source_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_slug: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str = Field(min_length=1, max_length=255)
    branch_name: str = Field(pattern=r"^devflow/run-[0-9a-f-]{36}$", max_length=255)
    publisher_configured: bool
    attempt_count: int = Field(ge=0)
    pull_request_number: int | None = Field(default=None, ge=1)
    pull_request_url: str | None = Field(default=None, max_length=2000)
    pull_request_state: str | None = Field(default=None, pattern=r"^(open|closed)$")
    pull_request_draft: bool | None = None
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=512)


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
    BLOCKED_BY_CONTRACT = "BLOCKED_BY_CONTRACT"


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
    execution_mode: WorkflowExecutionMode = WorkflowExecutionMode.AGENT
    workflow_id: WorkflowId | None = None
    workflow_step: str | None = Field(default=None, max_length=64)
    agent_escalation_reason: str | None = Field(default=None, max_length=512)
    contract_block_reason: str | None = Field(default=None, max_length=512)
    owned_paths: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    verification_commands: tuple[str, ...] = ()
    complexity: str | None = Field(default=None, pattern=r"^(LOW|MEDIUM|HIGH)$")
    package_budget_tokens: int | None = Field(default=None, ge=0)
    package_used_tokens: int | None = Field(default=None, ge=0)


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
    github_publication_token: SecretStr | None = Field(default=None, exclude=True)


class RunCreateRequest(ProductModel):
    project_id: UUID
    task: TaskContract


class ProductDependencyPreflight(ProductModel):
    profile_kind: str = Field(min_length=1, max_length=64)
    dependency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_manager: str = Field(min_length=1, max_length=32)
    manifest_paths: tuple[str, ...] = Field(max_length=32)
    packages: tuple[str, ...] = Field(max_length=256)
    cache_state: str = Field(pattern=r"^(HIT|MISS|NOT_REQUIRED)$")
    docker_version: str = Field(min_length=1, max_length=128)
    registry_url: str | None = Field(default=None, max_length=512)
    proxy_configured: bool
    required_runtimes: tuple[str, ...] = Field(min_length=1, max_length=2)


class ProductDependencyEnvironmentStatus(ProductModel):
    dependency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_kind: str = Field(min_length=1, max_length=64)
    package_manager: str = Field(min_length=1, max_length=32)
    cache_state: str = Field(pattern=r"^(HIT|MISS|NOT_REQUIRED)$")
    artifact_bytes: int = Field(ge=0)
    build_duration_ms: int | None = Field(default=None, ge=0)
    log_tail: str = Field(max_length=8_000)


class ProductDependencyEnvironmentMetrics(ProductModel):
    cache_hits: int = Field(ge=0)
    builds: int = Field(ge=0)
    failures: int = Field(ge=0)
    hit_rate: float = Field(ge=0.0, le=1.0)
    average_build_duration_ms: int = Field(ge=0)
    cache_bytes: int = Field(ge=0)


class ProductDependencyCacheCleanup(ProductModel):
    removed_fingerprints: tuple[str, ...]
    reclaimed_bytes: int = Field(ge=0)
    retained_bytes: int = Field(ge=0)


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
    dependency_preflight: ProductDependencyPreflight | None = None
