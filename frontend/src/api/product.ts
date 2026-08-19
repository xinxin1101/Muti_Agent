import { apiClient } from "./client";
import type {
  ProductProject,
  ProductRun,
  ProductRunDetail,
  ProductTaskDetail,
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

export function getTask(
  runId: string,
  taskId: string,
): Promise<ProductTaskDetail> {
  return apiClient.getJson<ProductTaskDetail>(
    `${API_PREFIX}/runs/${runId}/tasks/${encodeURIComponent(taskId)}`,
  );
}
