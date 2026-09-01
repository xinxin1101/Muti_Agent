export type ProductProject = Readonly<{
  project_id: string;
  repository_url: string;
  default_branch: string;
  created_at: string;
  run_count: number;
  workspace_ready: boolean;
  provision_status: "PROVISIONING" | "READY" | "FAILED" | "ARCHIVED";
  provision_error_code: string | null;
  provision_error_message: string | null;
  lifecycle_state?: "ACTIVE" | "ARCHIVED" | "DELETING" | "DELETED";
}>;

export type ProductRun = Readonly<{
  run_id: string;
  project_id: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  base_commit: string;
  task_count: number;
  started_at: string;
  finished_at: string | null;
  development_session_id?: string | null;
  visibility_status?: "VISIBLE" | "ARCHIVED";
  display_status?: "RUNNING" | "WAITING_EXTERNAL" | "RECOVERY_REQUIRED" | "FAILED" | "SUCCEEDED";
  recovery_reason?: string | null;
  recovery_checked_at?: string | null;
}>;

export type ProductRunRecoveryPreview = Readonly<{
  run_id: string;
  display_status: "RUNNING" | "WAITING_EXTERNAL" | "RECOVERY_REQUIRED" | "FAILED" | "SUCCEEDED";
  reason: string;
  observed_at: string;
  baseline_commit: string;
  current_commit: string;
  baseline_changed: boolean;
  dag_complete: boolean;
  reusable_task_ids: readonly string[];
  checkpointed_task_ids: readonly string[];
  remaining_task_ids: readonly string[];
  estimated_new_budget_tokens: number;
  existing_recovery_run_id: string | null;
  recovery_available: boolean;
  next_action: string;
}>;

export type ProductProjectDeletionPreview = Readonly<{
  project_id: string;
  required_confirmation_name: string;
  confirmation_token: string;
  confirmation_expires_at: string;
  run_count: number;
  development_session_count: number;
  local_workspace_bytes: number;
  project_cache_bytes: number;
  local_credential_count: number;
  github_repository_will_be_preserved: true;
}>;

export type ProjectDeletePayload = Readonly<{
  confirmation_token: string;
  confirmation_name: string;
}>;

export type ProductProjectDeletionResult = Readonly<{
  project_id: string;
  removed_run_count: number;
  removed_development_session_count: number;
  removed_local_workspace_bytes: number;
  removed_project_cache_bytes: number;
  removed_local_credential_count: number;
  github_repository_preserved: true;
}>;

export type ProductDevelopmentSessionRecovery = Readonly<{
  session_id: string;
  source_run_id: string | null;
  baseline_commit: string;
  current_commit: string;
  baseline_state: "UNCHANGED" | "CHANGED";
  reusable_work_package_ids: readonly string[];
  checkpointed_work_package_ids: readonly string[];
  remaining_work_package_ids: readonly string[];
  next_action: string;
  budget: Readonly<{
    planning_remaining_tokens: number | null;
    development_remaining_tokens: number | null;
    repair_remaining_tokens: number | null;
    estimated_new_development_tokens: number;
    estimated_tokens_saved: number;
  }>;
}>;

export type ProductDevelopmentSessionWorkPackage = Readonly<{
  task_id: string;
  state: "PENDING" | "RUNNING" | "SUCCEEDED" | "CHECKPOINTED" | "FAILED" | "BLOCKED";
  source_run_id: string | null;
  commit_sha: string | null;
  completed_interfaces: readonly string[];
  verification_summary: string;
  failure_summary: string;
  remaining_budget_tokens: number | null;
}>;

export type ProductDevelopmentSession = Readonly<{
  session_id: string;
  project_id: string;
  requirement: string;
  base_commit: string;
  state: "PLANNING" | "PAUSED_PLANNING" | "PLANNING_FAILED" | "READY_TO_RUN" | "RUNNING" | "COMPLETED";
  planning_diagnostic: string;
  latest_run_id: string | null;
  resumed_from_run_id: string | null;
  work_packages: readonly ProductDevelopmentSessionWorkPackage[];
  created_at: string;
  updated_at: string;
}>;

export type ProductDevelopmentSessionTimelineEntry = Readonly<{
  entry_id: number;
  session_id: string;
  kind:
    | "USER_REQUIREMENT"
    | "PLAN_DRAFT"
    | "BUDGET_DIAGNOSTIC"
    | "WORK_PACKAGE_SUCCEEDED"
    | "WORK_PACKAGE_FAILED"
    | "WORK_PACKAGE_CHECKPOINTED"
    | "RECOVERY_PREVIEW"
    | "USER_ACTION"
    | "RUN_LINKED";
  title: string;
  detail: string;
  run_id: string | null;
  task_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}>;

export type DevelopmentSessionCommandIntent =
  | "CONTINUE_DEVELOPMENT"
  | "CONTINUE_OLD_BASE"
  | "REPLAN"
  | "ARCHIVE_RUN"
  | "ARCHIVE_PROJECT"
  | "DELETE_PROJECT"
  | "UNKNOWN";

export type ProductDevelopmentSessionCommandPreview = Readonly<{
  session_id: string;
  intent: DevelopmentSessionCommandIntent;
  action_name: string;
  target_label: string;
  impact: readonly string[];
  token_cost: string;
  affects_local_data: boolean;
  confirmation_required: true;
  executable_after_confirmation: boolean;
  confirmation_hint: string;
}>;

export type ProductTaskSummary = Readonly<{
  task_id: string;
  objective: string;
  evidence_count: number;
}>;

export type ProductRunFailure = Readonly<{
  task_id: string | null;
  failure_type:
    | "MODEL_TIMEOUT"
    | "AGENT_TIME_LIMIT"
    | "RATE_LIMIT"
    | "INVALID_AGENT_OUTPUT"
    | "INVALID_TOOL_ARGUMENTS"
    | "TOOL_FAILURE"
    | "SCOPE_VIOLATION"
    | "TEST_FAILURE"
    | "LINT_FAILURE"
    | "REVIEW_REJECTED"
    | "CONTEXT_OVERFLOW"
    | "MERGE_CONFLICT"
    | "SANDBOX_TIMEOUT"
    | "VERIFICATION_ENV_UNAVAILABLE"
    | "TOKEN_BUDGET_EXHAUSTED"
    | "WORK_PACKAGE_BUDGET_ALLOCATION_BLOCKED"
    | "INTERFACE_CONTRACT_UNMET";
  source: "provider" | "tool" | "verification" | "review" | "runtime";
  message: string;
  retryable: boolean;
  evidence: readonly string[];
}>;

export type ProductFailureExplanation = Readonly<{
  run_id: string;
  failure_fingerprint: string;
  explanation: string;
  model: string;
  cached: boolean;
  created_at: string;
}>;

export type ProductRunCheckpoint = Readonly<{
  task_id: string;
  commit_sha: string;
  changed_files: readonly string[];
  reason: "TIME_LIMIT" | "ITERATION_LIMIT" | "TOOL_CALL_LIMIT" | "VERIFICATION_FAILURE";
  summary: string;
  remaining_budget_tokens?: number | null;
}>;

export type ProductRunDetail = ProductRun &
  Readonly<{
    repository_url: string;
    default_branch: string;
    tasks: readonly ProductTaskSummary[];
    failures?: readonly ProductRunFailure[];
    checkpoint?: ProductRunCheckpoint | null;
  }>;

export type ProductEvidenceMetrics = Readonly<{
  total_records: number;
  developer_runs: number;
  verification_attempts: number;
  review_decisions: number;
  repair_attempts: number;
  failure_reports: number;
  dispatch_events: number;
  worker_executions: number;
  merge_queue_snapshots: number;
  merge_conflicts: number;
  integration_gate_evaluations: number;
  human_decisions: number;
}>;

export type ProductRuntimeEventMetrics = Readonly<{
  total_events: number;
  warning_events: number;
  error_events: number;
  lease_acquisitions: number;
  lease_takeovers: number;
  lease_releases: number;
  latest_sequence: number;
}>;

export type ProductRunMetrics = Readonly<{
  run_id: string;
  project_id: string;
  status: ProductRun["status"];
  status_basis: "PERSISTED_RUN";
  task_count: number;
  started_at: string;
  finished_at: string | null;
  terminal_duration_ms: number | null;
  evidence: ProductEvidenceMetrics;
  runtime_events: ProductRuntimeEventMetrics;
  token_budget: ProductRunTokenBudget;
  planning_budget?: ProductPlanningTokenBudget | null;
  performance: ProductStagePerformanceMetrics;
  workflow: ProductWorkflowMetrics;
}>;

export type ProductWorkflowMetrics = Readonly<{
  activation_mode: "workflow_only" | "workflow_first" | "agent_only";
  workflow_tasks: number;
  agent_tasks: number;
  hybrid_tasks: number;
  workflow_calls: number;
  agent_calls: number;
  workflow_duration_ms: number;
  estimated_tokens_saved: number;
  agent_escalations: number;
}>;

export type ProductRoleTokenUsage = Readonly<{
  role: "planner" | "developer" | "reviewer" | "repair";
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  call_count: number;
}>;

export type ProductRunTokenBudget = Readonly<{
  total_budget_tokens: number;
  used_prompt_tokens: number;
  used_completion_tokens: number;
  used_total_tokens: number;
  reserved_tokens: number;
  status: "NORMAL" | "WARNING" | "CRITICAL" | "EXHAUSTED";
  roles: readonly ProductRoleTokenUsage[];
  stages?: readonly ProductStageTokenBudget[];
  work_packages?: readonly ProductWorkPackageTokenBudget[];
}>;

export type ProductStageTokenBudget = Readonly<{
  stage: string;
  total_budget_tokens: number;
  used_tokens: number;
  reserved_tokens: number;
}>;

export type ProductWorkPackageTokenBudget = Readonly<{
  task_id: string;
  complexity: "LOW" | "MEDIUM" | "HIGH";
  total_budget_tokens: number;
  developer_budget_tokens: number;
  repair_budget_tokens: number;
  developer_used_tokens: number;
  repair_used_tokens: number;
  developer_reserved_tokens: number;
  repair_reserved_tokens: number;
  developer_borrowed_tokens?: number;
  repair_borrowed_tokens?: number;
  developer_reclaimed_tokens?: number;
  repair_reclaimed_tokens?: number;
  developer_observed_prompt_tokens?: number;
  repair_observed_prompt_tokens?: number;
  developer_predicted_next_input_tokens?: number;
  repair_predicted_next_input_tokens?: number;
  developer_estimated_executable_turns?: number;
  repair_estimated_executable_turns?: number;
  developer_startup_reserve_tokens?: number;
  complexity_upgrade_count?: number;
  borrow_count?: number;
  last_required_tokens?: number;
  last_available_tokens?: number;
  last_flex_available_tokens?: number;
  last_downstream_available_tokens?: number;
  last_borrowed_tokens?: number;
  last_budget_decision?: string | null;
  last_budget_reason?: string | null;
  last_recovery_action?: string | null;
  last_cost_prediction_reason?: string | null;
  status: "ACTIVE" | "RECLAIMED";
}>;

export type ProductPlanningTokenBudget = Readonly<{
  total_budget_tokens: number;
  used_total_tokens: number;
  attempt_count: number;
  max_attempts: number;
  enable_thinking: boolean;
  status: "NORMAL" | "WARNING" | "CRITICAL" | "EXHAUSTED";
}>;

export type ProductStagePerformanceMetrics = Readonly<{
  developer_model_latency_ms: number;
  repair_model_latency_ms: number;
  repository_tool_latency_ms: number;
  verification_latency_ms: number;
  context_estimated_tokens: number;
  estimated_prompt_tokens?: number;
  actual_prompt_tokens?: number;
  prompt_estimate_error_ratio?: number;
  context_reused_files: number;
  context_trimmed_files: number;
  context_compacted_tool_groups?: number;
}>;

export type ProductDAGNodeState =
  | "PENDING"
  | "READY"
  | "RUNNING"
  | "VERIFYING"
  | "REVIEWING"
  | "REPAIRING"
  | "SUCCEEDED"
  | "FAILED"
  | "BLOCKED"
  | "BLOCKED_BY_CONTRACT";

export type ProductDAGNode = Readonly<{
  task_id: string;
  objective: string;
  depends_on: readonly string[];
  topological_index: number;
  layer: number;
  presentation_state: ProductDAGNodeState;
  state_basis: "EVIDENCE" | "DERIVED_DAG";
  execution_mode?: "WORKFLOW" | "AGENT" | "HYBRID";
  workflow_id?:
    | "python-script"
    | "node-script"
    | "dependency-preflight"
    | "verification"
    | "git-publication"
    | null;
  workflow_step?: string | null;
  agent_escalation_reason?: string | null;
  contract_block_reason?: string | null;
  owned_paths?: readonly string[];
  consumes?: readonly string[];
  produces?: readonly string[];
  verification_commands?: readonly string[];
  complexity?: "LOW" | "MEDIUM" | "HIGH" | null;
  package_budget_tokens?: number | null;
  package_used_tokens?: number | null;
}>;

export type ProductDAGEdge = Readonly<{
  source_task_id: string;
  target_task_id: string;
}>;

export type ProductRunDAG = Readonly<{
  run_id: string;
  dag_sha256: string;
  topology_source: "PERSISTED" | "IMPLICIT_SINGLE_TASK";
  topological_order: readonly string[];
  nodes: readonly ProductDAGNode[];
  edges: readonly ProductDAGEdge[];
}>;

export type ProductDiffKind = "TASK" | "INTEGRATION";
export type ProductDiffEvidenceBasis =
  | "WORKER_EXECUTION"
  | "MERGE_QUEUE_SNAPSHOT";
export type ProductDiffFileStatus =
  | "ADDED"
  | "MODIFIED"
  | "DELETED"
  | "TYPE_CHANGED";
export type ProductDiffOmissionReason =
  | "BINARY"
  | "BLOB_LIMIT"
  | "TOTAL_PATCH_LIMIT";

export type ProductDiffFile = Readonly<{
  path: string;
  status: ProductDiffFileStatus;
  additions: number | null;
  deletions: number | null;
  binary: boolean;
  patch: string | null;
  patch_bytes: number;
  patch_sha256: string | null;
  patch_truncated: boolean;
  patch_omitted_reason: ProductDiffOmissionReason | null;
}>;

export type ProductTaskDiff = Readonly<{
  run_id: string;
  project_id: string;
  task_id: string;
  diff_kind: ProductDiffKind;
  evidence_basis: ProductDiffEvidenceBasis;
  source_evidence_id: number;
  source_evidence_sha256: string;
  base_commit: string;
  head_commit: string;
  changed_file_count: number;
  additions: number;
  deletions: number;
  files: readonly ProductDiffFile[];
  omitted_file_count: number;
  patch_bytes: number;
  truncated: boolean;
}>;

export type TaskContractPayload = Readonly<{
  task_id: string;
  objective: string;
  readable_files: readonly string[];
  writable_files: readonly string[];
  readonly_files: readonly string[];
  acceptance_criteria: readonly string[];
  verification_commands: readonly string[];
  max_retries: number;
}>;

export type ProductEvidenceSummary = Readonly<{
  evidence_id: number;
  kind: string;
  stage: string | null;
  sequence: number | null;
  payload_sha256: string;
  created_at: string;
}>;

export type ProductTaskDetail = Readonly<{
  run_id: string;
  project_id: string;
  run_status: ProductRun["status"];
  task: TaskContractPayload;
  contract_sha256: string;
  created_at: string;
  evidence: readonly ProductEvidenceSummary[];
}>;

export type RunLaunchResponse = Readonly<{
  run_id: string;
  project_id: string;
  task_id: string;
  base_commit: string;
  dispatch_status: "QUEUED" | "BROKER_UNAVAILABLE";
  dispatch_id: string | null;
  broker_message_id: string | null;
  queue_name: string | null;
  detail: string | null;
}>;

export type ProjectCreatePayload = Readonly<{
  repository_url: string;
  default_branch: string;
  github_publication_token: string;
}>;

export type RunCreatePayload = Readonly<{
  project_id: string;
  task: TaskContractPayload;
}>;
