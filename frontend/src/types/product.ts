export type ProductProject = Readonly<{
  project_id: string;
  repository_url: string;
  default_branch: string;
  created_at: string;
  run_count: number;
  workspace_ready: boolean;
}>;

export type ProductRun = Readonly<{
  run_id: string;
  project_id: string;
  status: "RUNNING" | "SUCCEEDED" | "FAILED";
  base_commit: string;
  task_count: number;
  started_at: string;
  finished_at: string | null;
}>;

export type ProductTaskSummary = Readonly<{
  task_id: string;
  objective: string;
  evidence_count: number;
}>;

export type ProductRunDetail = ProductRun &
  Readonly<{
    repository_url: string;
    default_branch: string;
    tasks: readonly ProductTaskSummary[];
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
  | "BLOCKED";

export type ProductDAGNode = Readonly<{
  task_id: string;
  objective: string;
  depends_on: readonly string[];
  topological_index: number;
  layer: number;
  presentation_state: ProductDAGNodeState;
  state_basis: "EVIDENCE" | "DERIVED_DAG";
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
}>;

export type RunCreatePayload = Readonly<{
  project_id: string;
  task: TaskContractPayload;
}>;
