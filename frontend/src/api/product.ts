import type { ProductGitHubPublication } from "../types/publication";
import type {
  ProductDiffKind,
  ProductProject,
  ProductRun,
  ProductRunDAG,
  ProductRunDetail,
  ProductRunMetrics,
  ProductTaskDetail,
  ProductTaskDiff,
  ProjectCreatePayload,
  RunCreatePayload,
  RunLaunchResponse,
} from "../types/product";
import { apiClient } from "./client";

const API_PREFIX = "/api/v1";

export type RequirementRunCreatePayload = Readonly<{
  project_id: string;
  requirement: string;
}>;

export type RequirementTaskDispatch = Readonly<{
  task_id: string;
  state: "QUEUED" | "BROKER_UNAVAILABLE";
  dispatch_id: string | null;
  broker_message_id: string | null;
  queue_name: string | null;
  detail: string | null;
}>;

export type RequirementRunLaunchResponse = Readonly<{
  run_id: string;
  project_id: string;
  base_commit: string;
  dag_sha256: string;
  task_ids: readonly string[];
  initial_ready_task_ids: readonly string[];
  launch_state: "QUEUED" | "PARTIAL" | "BROKER_UNAVAILABLE";
  dispatches: readonly RequirementTaskDispatch[];
}>;

export type HumanGateDecisionName = "AUTHORIZE_REPAIR" | "ABORT";
export type IntegrationGateStateName =
  | "AUTO_REPAIR_CANDIDATE"
  | "AWAITING_HUMAN"
  | "REPAIR_AUTHORIZED"
  | "ABORTED";

export type HumanGateSnapshot = Readonly<{
  task_id: string;
  task_branch: string;
  task_commit: string;
  integration_ref: string;
  integration_head: string;
  conflict_ref: string;
  conflict_marker_commit: string;
  evidence_fingerprint: string;
  policy_fingerprint: string;
  policy: Readonly<{
    route: "AUTO_REPAIR_CANDIDATE" | "HUMAN_REQUIRED";
    evidence_fingerprint: string;
    automatic_repair_enabled: boolean;
    human_repair_authorizable: boolean;
    conflicting_paths: readonly string[];
    reasons: readonly string[];
  }>;
  state: IntegrationGateStateName;
  human_decision: Readonly<{
    decision: HumanGateDecisionName;
    actor: string;
    note: string;
    decision_ref: string;
    decision_commit: string;
    evidence_fingerprint: string;
    policy_fingerprint: string;
    conflict_marker_commit: string;
  }> | null;
  repair_may_start: boolean;
  integration_may_advance: false;
}>;

export type HumanGateDecisionPayload = Readonly<{
  evidence_fingerprint: string;
  decision: HumanGateDecisionName;
  note?: string;
}>;

export type OperatorAction = Readonly<{
  action_id: string;
  kind: "ADVANCE_RUN";
  label: string;
  description: string;
}>;

export type OperatorRecoveryTask = Readonly<{
  run_id: string;
  task_id: string;
  depends_on: readonly string[];
  topological_index: number;
  frontier_state:
    | "RUN_TERMINAL"
    | "SUCCEEDED"
    | "FAILED"
    | "BLOCKED_UPSTREAM_FAILURE"
    | "WAIT_DEPENDENCIES"
    | "WAIT_ACTIVE_OWNER"
    | "BLOCKED_RECOVERY_GAP"
    | "WAIT_INTEGRATION_BASE"
    | "RECONCILE_CANDIDATE";
  lease_state: "UNOWNED" | "ACTIVE" | "EXPIRED" | "RELEASED";
  lease_generation: number;
  lease_dispatch_id: string | null;
  worker_execution_status: "SUCCEEDED" | "FAILED" | null;
  worker_execution_evidence_id: number | null;
  reason: string;
}>;

export type OperatorRecoveryPlan = Readonly<{
  run_id: string;
  diagnostic_only: true;
  mutation_requires_fresh_revalidation: true;
  reconciliation: Readonly<{
    run_id: string;
    run_status: "RUNNING" | "SUCCEEDED" | "FAILED";
    dag_sha256: string;
    topology_source: "PERSISTED" | "LEGACY_SINGLE_TASK";
    observed_at: string;
    topological_order: readonly string[];
    completed_task_ids: readonly string[];
    failed_task_ids: readonly string[];
    blocked_task_ids: readonly string[];
    ready_task_ids: readonly string[];
    reconcile_task_ids: readonly string[];
    tasks: readonly OperatorRecoveryTask[];
  }>;
  actions: readonly OperatorAction[];
}>;

export type OperatorActionExecutionResult = Readonly<{
  run_id: string;
  action: OperatorAction;
  request_evidence_id: number;
  execution_delegated: true;
  refreshed_plan: OperatorRecoveryPlan;
}>;

export function listProjects(): Promise<readonly ProductProject[]> {
  return apiClient.getJson<readonly ProductProject[]>(`${API_PREFIX}/projects`);
}

export function createProject(
  payload: ProjectCreatePayload,
): Promise<ProductProject> {
  return apiClient.postJson<ProductProject, ProjectCreatePayload>(`${API_PREFIX}/projects`, payload);
}

export function getProject(projectId: string): Promise<ProductProject> {
  return apiClient.getJson<ProductProject>(`${API_PREFIX}/projects/${projectId}`);
}

export function listRuns(projectId?: string): Promise<readonly ProductRun[]> {
  const query = projectId
    ? `?project_id=${encodeURIComponent(projectId)}`
    : "";
  return apiClient.getJson<readonly ProductRun[]>(`${API_PREFIX}/runs${query}`);
}

/** Legacy V1 TaskContract launch kept for benchmark/backward compatibility. */
export function createRun(payload: RunCreatePayload): Promise<RunLaunchResponse> {
  return apiClient.postJson<RunLaunchResponse, RunCreatePayload>(`${API_PREFIX}/runs`, payload);
}

/** Phase 6 product entry: the browser supplies intent, never a TaskContract or Git authority. */
export function createRequirementRun(
  payload: RequirementRunCreatePayload,
): Promise<RequirementRunLaunchResponse> {
  return apiClient.postJson<RequirementRunLaunchResponse, RequirementRunCreatePayload>(
    `${API_PREFIX}/runs/from-requirement`,
    payload,
  );
}

export function listHumanGates(runId: string): Promise<readonly HumanGateSnapshot[]> {
  return apiClient.getJson<readonly HumanGateSnapshot[]>(
    `${API_PREFIX}/runs/${runId}/human-gates`,
  );
}

export function decideHumanGate(
  runId: string,
  taskId: string,
  payload: HumanGateDecisionPayload,
): Promise<HumanGateSnapshot> {
  return apiClient.postJson<HumanGateSnapshot, HumanGateDecisionPayload>(
    `${API_PREFIX}/runs/${runId}/human-gates/${encodeURIComponent(taskId)}/decision`,
    payload,
  );
}

export function getOperatorRecovery(runId: string): Promise<OperatorRecoveryPlan> {
  return apiClient.getJson<OperatorRecoveryPlan>(
    `${API_PREFIX}/runs/${runId}/operator-recovery`,
  );
}

export function executeOperatorAction(
  runId: string,
  actionId: string,
): Promise<OperatorActionExecutionResult> {
  return apiClient.postNoBody<OperatorActionExecutionResult>(
    `${API_PREFIX}/runs/${runId}/operator-actions/${encodeURIComponent(actionId)}`,
  );
}

export function getRun(runId: string): Promise<ProductRunDetail> {
  return apiClient.getJson<ProductRunDetail>(`${API_PREFIX}/runs/${runId}`);
}

export function getRunMetrics(runId: string): Promise<ProductRunMetrics> {
  return apiClient.getJson<ProductRunMetrics>(`${API_PREFIX}/runs/${runId}/metrics`);
}

export function getGitHubPublication(runId: string): Promise<ProductGitHubPublication> {
  return apiClient.getJson<ProductGitHubPublication>(
    `${API_PREFIX}/runs/${runId}/github-publication`,
  );
}

export function publishGitHubDraft(runId: string): Promise<ProductGitHubPublication> {
  return apiClient.postNoBody<ProductGitHubPublication>(
    `${API_PREFIX}/runs/${runId}/github-publication`,
  );
}

export function getRunDAG(runId: string): Promise<ProductRunDAG> {
  return apiClient.getJson<ProductRunDAG>(`${API_PREFIX}/runs/${runId}/dag`);
}

export function getTask(
  runId: string,
  taskId: string,
): Promise<ProductTaskDetail> {
  return apiClient.getJson<ProductTaskDetail>(
    `${API_PREFIX}/runs/${runId}/tasks/${encodeURIComponent(taskId)}`,
  );
}

export function getTaskDiff(
  runId: string,
  taskId: string,
  kind: ProductDiffKind = "TASK",
): Promise<ProductTaskDiff> {
  return apiClient.getJson<ProductTaskDiff>(
    `${API_PREFIX}/runs/${runId}/tasks/${encodeURIComponent(taskId)}/diff?kind=${kind}`,
  );
}
