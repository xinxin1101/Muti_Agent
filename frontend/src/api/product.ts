import { apiClient } from "./client";
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
