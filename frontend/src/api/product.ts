import type { ProductGitHubPublication } from "../types/publication";
import type {
  ProductDiffKind,
  ProductDevelopmentSessionRecovery,
  ProductDevelopmentSession,
  ProductDevelopmentSessionCommandPreview,
  ProductDevelopmentSessionTimelineEntry,
  ProductFailureExplanation,
  ProductProject,
  ProductProjectDeletionPreview,
  ProductProjectDeletionResult,
  ProductRun,
  ProductRunRecoveryPreview,
  ProductRunDAG,
  ProductRunDetail,
  ProductRunMetrics,
  ProductTaskDetail,
  ProductTaskDiff,
  ProjectCreatePayload,
  ProjectDeletePayload,
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

/** Server-verified dependency facts collected before a Run is created or dispatched. */
export type DependencyPreflight = Readonly<{
  profile_kind: "PYTHON_BASE" | "PYTHON_PINNED_REQUIREMENTS" | "NODE_NPM_LOCK";
  dependency_fingerprint: string;
  package_manager: "NONE" | "PYTHON" | "NODE" | "MIXED";
  manifest_paths: readonly string[];
  packages: readonly string[];
  cache_state: "HIT" | "MISS" | "NOT_REQUIRED";
  docker_version: string;
  registry_url: string | null;
  proxy_configured: boolean;
  required_runtimes: readonly ("PYTHON" | "NODE")[];
}>;

export type DependencyEnvironmentStatus = Readonly<{
  dependency_fingerprint: string;
  profile_kind: string;
  package_manager: string;
  cache_state: "HIT" | "MISS" | "NOT_REQUIRED";
  artifact_bytes: number;
  build_duration_ms: number | null;
  log_tail: string;
}>;

export type DependencyEnvironmentMetrics = Readonly<{
  cache_hits: number;
  builds: number;
  failures: number;
  hit_rate: number;
  average_build_duration_ms: number;
  cache_bytes: number;
}>;

export type DependencyCacheCleanup = Readonly<{
  removed_fingerprints: readonly string[];
  reclaimed_bytes: number;
  retained_bytes: number;
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
  dependency_preflight?: DependencyPreflight | null;
  resumed_from_run_id?: string | null;
  reused_existing_run?: boolean;
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

export type RuntimeDependencyHealth = Readonly<{
  runtime_fingerprint: string;
  database: Readonly<{ state: "READY" | "NOT_CONFIGURED" | "UNAVAILABLE" | "MODEL_UNAVAILABLE"; detail: string }>;
  redis: Readonly<{ state: "READY" | "NOT_CONFIGURED" | "UNAVAILABLE" | "MODEL_UNAVAILABLE"; detail: string }>;
  dispatch_available: boolean;
}>;

export function listProjects(): Promise<readonly ProductProject[]>;
export function listProjects(includeArchived: boolean): Promise<readonly ProductProject[]>;
export function listProjects(includeArchived = false): Promise<readonly ProductProject[]> {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiClient.getJson<readonly ProductProject[]>(`${API_PREFIX}/projects${query}`);
}

export function createProject(
  payload: ProjectCreatePayload,
): Promise<ProductProject> {
  return apiClient.postJson<ProductProject, ProjectCreatePayload>(`${API_PREFIX}/projects`, payload);
}

export function getProject(projectId: string): Promise<ProductProject> {
  return apiClient.getJson<ProductProject>(`${API_PREFIX}/projects/${projectId}`);
}

export function archiveProject(projectId: string): Promise<ProductProject> {
  return apiClient.postNoBody<ProductProject>(`${API_PREFIX}/projects/${projectId}/archive`);
}

export function restoreProject(projectId: string): Promise<ProductProject> {
  return apiClient.postNoBody<ProductProject>(`${API_PREFIX}/projects/${projectId}/restore`);
}

export function getProjectDeletionPreview(projectId: string): Promise<ProductProjectDeletionPreview> {
  return apiClient.getJson<ProductProjectDeletionPreview>(
    `${API_PREFIX}/projects/${projectId}/deletion-preview`,
  );
}

export function deleteProject(
  projectId: string,
  payload: ProjectDeletePayload,
): Promise<ProductProjectDeletionResult> {
  return apiClient.deleteJson<ProductProjectDeletionResult, ProjectDeletePayload>(
    `${API_PREFIX}/projects/${projectId}`,
    payload,
  );
}

export function listRuns(projectId?: string, includeArchived = false): Promise<readonly ProductRun[]> {
  const values = new URLSearchParams();
  if (projectId) values.set("project_id", projectId);
  if (includeArchived) values.set("include_archived", "true");
  const query = values.size ? `?${values.toString()}` : "";
  return apiClient.getJson<readonly ProductRun[]>(`${API_PREFIX}/runs${query}`);
}

export function archiveRun(runId: string): Promise<void> {
  return apiClient.postNoBody<void>(`${API_PREFIX}/runs/${runId}/archive`);
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

/** Replays only the failed Run's server-persisted DAG as a new audited Run. */
export function retryRun(runId: string): Promise<RequirementRunLaunchResponse> {
  return apiClient.postNoBody<RequirementRunLaunchResponse>(
    `${API_PREFIX}/runs/${runId}/retry`,
  );
}

/** Continues from a server-created, fenced checkpoint when the failed Run exposes one. */
export function resumeRun(runId: string): Promise<RequirementRunLaunchResponse> {
  return apiClient.postNoBody<RequirementRunLaunchResponse>(
    `${API_PREFIX}/runs/${runId}/resume`,
  );
}

export function getDevelopmentSessionRecovery(
  sessionId: string,
): Promise<ProductDevelopmentSessionRecovery> {
  return apiClient.getJson<ProductDevelopmentSessionRecovery>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}/recovery-preview`,
  );
}

export function listDevelopmentSessions(
  projectId: string,
): Promise<readonly ProductDevelopmentSession[]> {
  return apiClient.getJson<readonly ProductDevelopmentSession[]>(
    `${API_PREFIX}/projects/${encodeURIComponent(projectId)}/development-sessions`,
  );
}

export function getDevelopmentSession(
  sessionId: string,
): Promise<ProductDevelopmentSession> {
  return apiClient.getJson<ProductDevelopmentSession>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}`,
  );
}

export function getDevelopmentSessionTimeline(
  sessionId: string,
): Promise<readonly ProductDevelopmentSessionTimelineEntry[]> {
  return apiClient.getJson<readonly ProductDevelopmentSessionTimelineEntry[]>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}/timeline`,
  );
}

export function previewDevelopmentSessionCommand(
  sessionId: string,
  command: string,
): Promise<ProductDevelopmentSessionCommandPreview> {
  return apiClient.postJson<ProductDevelopmentSessionCommandPreview, Readonly<{ command: string }>>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}/command-preview`,
    { command },
  );
}

export function continueDevelopmentSession(
  sessionId: string,
  mode: "AUTO" | "OLD_BASE" = "AUTO",
): Promise<RequirementRunLaunchResponse> {
  return apiClient.postNoBody<RequirementRunLaunchResponse>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}/continue?mode=${mode}`,
  );
}

export function replanDevelopmentSession(
  sessionId: string,
): Promise<RequirementRunLaunchResponse> {
  return apiClient.postNoBody<RequirementRunLaunchResponse>(
    `${API_PREFIX}/development-sessions/${encodeURIComponent(sessionId)}/replan`,
  );
}

/** Starts a new Run only when the server confirms that the prior Run was interrupted mid-flight. */
export function recoverInterruptedRun(runId: string): Promise<RequirementRunLaunchResponse> {
  return apiClient.postNoBody<RequirementRunLaunchResponse>(
    `${API_PREFIX}/runs/${runId}/recover-as-new`,
  );
}

export function getRunRecoveryPreview(runId: string): Promise<ProductRunRecoveryPreview> {
  return apiClient.getJson<ProductRunRecoveryPreview>(
    `${API_PREFIX}/runs/${runId}/recovery-preview`,
  );
}

export function explainRunFailure(runId: string): Promise<ProductFailureExplanation> {
  return apiClient.postNoBody<ProductFailureExplanation>(
    `${API_PREFIX}/runs/${runId}/failure-explanation`,
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

export function getRuntimeDependencyHealth(): Promise<RuntimeDependencyHealth> {
  return apiClient.getJson<RuntimeDependencyHealth>(`${API_PREFIX}/runtime-health`);
}

export function getDependencyEnvironment(projectId: string): Promise<DependencyEnvironmentStatus> {
  return apiClient.getJson<DependencyEnvironmentStatus>(
    `${API_PREFIX}/projects/${encodeURIComponent(projectId)}/dependency-environment`,
  );
}

export function rebuildDependencyEnvironment(projectId: string): Promise<DependencyEnvironmentStatus> {
  return apiClient.postNoBody<DependencyEnvironmentStatus>(
    `${API_PREFIX}/projects/${encodeURIComponent(projectId)}/dependency-environment/rebuild`,
  );
}

export function getDependencyEnvironmentMetrics(): Promise<DependencyEnvironmentMetrics> {
  return apiClient.getJson<DependencyEnvironmentMetrics>(
    `${API_PREFIX}/dependency-environment/metrics`,
  );
}

export function cleanupDependencyEnvironments(): Promise<DependencyCacheCleanup> {
  return apiClient.postNoBody<DependencyCacheCleanup>(
    `${API_PREFIX}/dependency-environment/cleanup`,
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
