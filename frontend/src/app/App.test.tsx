import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import * as productApi from "../api/product";

vi.mock("../api/product");

const project = {
  project_id: "11111111-1111-1111-1111-111111111111",
  repository_url: "https://github.com/example/repo",
  default_branch: "main",
  created_at: "2026-08-19T00:00:00Z",
  run_count: 1,
  workspace_ready: true,
} as const;

const run = {
  run_id: "22222222-2222-2222-2222-222222222222",
  project_id: project.project_id,
  status: "RUNNING" as const,
  base_commit: "a".repeat(40),
  task_count: 1,
  started_at: "2026-08-19T00:00:00Z",
  finished_at: null,
} as const;

function renderApp(path: string) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.listProjects).mockResolvedValue([project]);
  vi.mocked(productApi.listRuns).mockResolvedValue([run]);
  vi.mocked(productApi.getRun).mockResolvedValue({
    ...run,
    repository_url: project.repository_url,
    default_branch: project.default_branch,
    tasks: [
      {
        task_id: "task-1",
        objective: "Build the product page.",
        evidence_count: 2,
      },
    ],
  });
  vi.mocked(productApi.getRunMetrics).mockResolvedValue({
    run_id: run.run_id,
    project_id: project.project_id,
    status: "RUNNING",
    status_basis: "PERSISTED_RUN",
    task_count: 1,
    started_at: run.started_at,
    finished_at: null,
    terminal_duration_ms: null,
    evidence: {
      total_records: 2,
      developer_runs: 0,
      verification_attempts: 1,
      review_decisions: 0,
      repair_attempts: 0,
      failure_reports: 0,
      dispatch_events: 0,
      worker_executions: 1,
      merge_queue_snapshots: 0,
      merge_conflicts: 0,
      integration_gate_evaluations: 0,
      human_decisions: 0,
    },
    runtime_events: {
      total_events: 1,
      warning_events: 0,
      error_events: 0,
      lease_acquisitions: 0,
      lease_takeovers: 0,
      lease_releases: 0,
      latest_sequence: 1,
    },
  });
  vi.mocked(productApi.getRunDAG).mockResolvedValue({
    run_id: run.run_id,
    dag_sha256: "d".repeat(64),
    topology_source: "PERSISTED",
    topological_order: ["task-1"],
    nodes: [
      {
        task_id: "task-1",
        objective: "Build the product page.",
        depends_on: [],
        topological_index: 0,
        layer: 0,
        presentation_state: "READY",
        state_basis: "DERIVED_DAG",
      },
    ],
    edges: [],
  });
  vi.mocked(productApi.getTask).mockResolvedValue({
    run_id: run.run_id,
    project_id: project.project_id,
    run_status: "RUNNING",
    task: {
      task_id: "task-1",
      objective: "Build the product page.",
      readable_files: ["frontend/src/**"],
      writable_files: ["frontend/src/pages/**"],
      readonly_files: ["frontend/src/app/App.test.tsx"],
      acceptance_criteria: ["The browser renders backend truth."],
      verification_commands: ["npm run test"],
      max_retries: 1,
    },
    contract_sha256: "b".repeat(64),
    created_at: "2026-08-19T00:00:00Z",
    evidence: [
      {
        evidence_id: 1,
        kind: "VERIFICATION_RESULT",
        stage: "verification",
        sequence: 0,
        payload_sha256: "c".repeat(64),
        created_at: "2026-08-19T00:01:00Z",
      },
    ],
  });
  vi.mocked(productApi.getTaskDiff).mockResolvedValue({
    run_id: run.run_id,
    project_id: project.project_id,
    task_id: "task-1",
    diff_kind: "TASK",
    evidence_basis: "WORKER_EXECUTION",
    source_evidence_id: 2,
    source_evidence_sha256: "d".repeat(64),
    base_commit: "a".repeat(40),
    head_commit: "b".repeat(40),
    changed_file_count: 1,
    additions: 1,
    deletions: 0,
    files: [
      {
        path: "frontend/src/pages/TaskDetailPage.tsx",
        status: "MODIFIED",
        additions: 1,
        deletions: 0,
        binary: false,
        patch: "+render backend diff\n",
        patch_bytes: 21,
        patch_sha256: "e".repeat(64),
        patch_truncated: false,
        patch_omitted_reason: null,
      },
    ],
    omitted_file_count: 0,
    patch_bytes: 21,
    truncated: false,
  });
});

describe("App", () => {
  it("keeps the Step 4.1 foundation route available", () => {
    renderApp("/");
    expect(
      screen.getByRole("heading", { name: "DevFlow product foundation" }),
    ).toBeInTheDocument();
  });

  it("renders real project data instead of a placeholder", async () => {
    renderApp("/projects");
    expect(
      await screen.findByText("https://github.com/example/repo"),
    ).toBeInTheDocument();
    expect(screen.getByText(/workspace ready/)).toBeInTheDocument();
  });

  it("renders the Phase 6 requirement-only New Run form", async () => {
    renderApp(`/runs/new?projectId=${project.project_id}`);
    expect(await screen.findByRole("heading", { name: "New Run" })).toBeInTheDocument();
    expect(screen.getByLabelText("Requirement")).toBeInTheDocument();
    expect(screen.queryByLabelText("Task ID")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Base commit")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Writable files/)).not.toBeInTheDocument();
  });

  it("submits only project identity and natural-language requirement", async () => {
    vi.mocked(productApi.createRequirementRun).mockResolvedValue({
      run_id: run.run_id,
      project_id: project.project_id,
      base_commit: run.base_commit,
      dag_sha256: "d".repeat(64),
      task_ids: ["auth-model", "auth-api"],
      initial_ready_task_ids: ["auth-model"],
      launch_state: "QUEUED",
      dispatches: [
        {
          task_id: "auth-model",
          state: "QUEUED",
          dispatch_id: "33333333-3333-3333-3333-333333333333",
          broker_message_id: "message-1",
          queue_name: "devflow_tasks",
          detail: null,
        },
      ],
    });

    renderApp(`/runs/new?projectId=${project.project_id}`);
    fireEvent.change(await screen.findByLabelText("Requirement"), {
      target: { value: "Add JWT login and refresh tokens with deterministic tests." },
    });
    await screen.findByRole("option", { name: project.repository_url });
    fireEvent.click(screen.getByRole("button", { name: "Start Multi-Agent run" }));

    await waitFor(() =>
      expect(productApi.createRequirementRun).toHaveBeenCalledTimes(1),
    );
    const payload = vi.mocked(productApi.createRequirementRun).mock.calls[0]?.[0];
    expect(payload).toEqual({
      project_id: project.project_id,
      requirement: "Add JWT login and refresh tokens with deterministic tests.",
    });
    expect(payload).not.toHaveProperty("base_commit");
    expect(payload).not.toHaveProperty("task");
  });

  it("renders the Run Dashboard from backend status", async () => {
    renderApp(`/runs/${run.run_id}`);
    expect(
      await screen.findByRole("heading", { name: "Run Dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("RUNNING").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Build the product page.").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("img", { name: "Validated task dependency DAG" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Run metrics" })).toBeInTheDocument();
  });

  it("renders Task Detail contract, diff, and evidence metadata", async () => {
    renderApp(`/runs/${run.run_id}/tasks/task-1`);
    expect(
      await screen.findByRole("heading", { name: "task-1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("VERIFICATION_RESULT")).toBeInTheDocument();
    expect(screen.getByText("frontend/src/pages/**")).toBeInTheDocument();
    expect(await screen.findByLabelText("Read-only Git diff")).toBeInTheDocument();
  });

  it("renders a bounded not-found state", () => {
    renderApp("/outside-step-4-2");
    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });
});
