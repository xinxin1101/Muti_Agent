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

  it("renders the New Run TaskContract form", async () => {
    renderApp(`/runs/new?projectId=${project.project_id}`);
    expect(await screen.findByRole("heading", { name: "New Run" })).toBeInTheDocument();
    expect(screen.getByLabelText("Task ID")).toHaveValue("task-1");
    expect(screen.queryByLabelText("Base commit")).not.toBeInTheDocument();
  });

  it("submits a validated run payload without browser base_commit", async () => {
    vi.mocked(productApi.createRun).mockResolvedValue({
      run_id: run.run_id,
      project_id: project.project_id,
      task_id: "task-1",
      base_commit: run.base_commit,
      dispatch_status: "QUEUED",
      dispatch_id: "33333333-3333-3333-3333-333333333333",
      broker_message_id: "message-1",
      queue_name: "devflow_tasks",
      detail: null,
    });

    renderApp(`/runs/new?projectId=${project.project_id}`);
    fireEvent.change(await screen.findByLabelText("Objective"), {
      target: { value: "Implement the product pages." },
    });
    fireEvent.change(screen.getByLabelText(/Writable files/), {
      target: { value: "frontend/src/pages/**" },
    });
    fireEvent.change(screen.getByLabelText(/Acceptance criteria/), {
      target: { value: "Product pages render." },
    });
    await screen.findByRole("option", { name: project.repository_url });
    fireEvent.click(screen.getByRole("button", { name: "Start run" }));

    await waitFor(() => expect(productApi.createRun).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(productApi.createRun).mock.calls[0]?.[0];
    expect(payload).toBeDefined();
    expect(payload).not.toHaveProperty("base_commit");
    expect(payload?.task.writable_files).toEqual(["frontend/src/pages/**"]);
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
  });

  it("renders Task Detail contract and evidence metadata", async () => {
    renderApp(`/runs/${run.run_id}/tasks/task-1`);
    expect(
      await screen.findByRole("heading", { name: "task-1" }),
    ).toBeInTheDocument();
    expect(screen.getByText("VERIFICATION_RESULT")).toBeInTheDocument();
    expect(screen.getByText("frontend/src/pages/**")).toBeInTheDocument();
  });

  it("renders a bounded not-found state", () => {
    renderApp("/outside-step-4-2");
    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });
});
