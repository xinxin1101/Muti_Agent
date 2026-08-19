import { apiClient } from "./client";
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

export function createRun(payload: RunCreatePayload): Promise<RunLaunchResponse> {
  return apiClient.postJson<RunLaunchResponse, RunCreatePayload>(`${API_PREFIX}/runs`, payload);
}

export function getRun(runId: string): Promise<ProductRunDetail> {
  return apiClient.getJson<ProductRunDetail>(`${API_PREFIX}/runs/${runId}`);
}

export function getRunMetrics(runId: string): Promise<ProductRunMetrics> {
  return apiClient.getJson<ProductRunMetrics>(`${API_PREFIX}/runs/${runId}/metrics`);
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