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
