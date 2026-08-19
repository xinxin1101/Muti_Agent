import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { TaskDetailPage } from "./TaskDetailPage";

vi.mock("../api/product");

const runId = "22222222-2222-2222-2222-222222222222";
const projectId = "11111111-1111-1111-1111-111111111111";

function renderTask() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/runs/${runId}/tasks/task-1`]}>
        <Routes>
          <Route path="/runs/:runId/tasks/:taskId" element={<TaskDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function diff(kind: "TASK" | "INTEGRATION") {
  return {
    run_id: runId,
    project_id: projectId,
    task_id: "task-1",
    diff_kind: kind,
    evidence_basis: kind === "TASK" ? "WORKER_EXECUTION" : "MERGE_QUEUE_SNAPSHOT",
    source_evidence_id: kind === "TASK" ? 7 : 9,
    source_evidence_sha256: "d".repeat(64),
    base_commit: "a".repeat(40),
    head_commit: "b".repeat(40),
    changed_file_count: 1,
    additions: 1,
    deletions: 1,
    files: [
      {
        path: "src/app.py",
        status: "MODIFIED" as const,
        additions: 1,
        deletions: 1,
        binary: false,
        patch: "@@ -1 +1 @@\n-old = 1\n+new = 2\n",
        patch_bytes: 35,
        patch_sha256: "e".repeat(64),
        patch_truncated: false,
        patch_omitted_reason: null,
      },
    ],
    omitted_file_count: 0,
    patch_bytes: 35,
    truncated: false,
  } as const;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.getTask).mockResolvedValue({
    run_id: runId,
    project_id: projectId,
    run_status: "SUCCEEDED",
    task: {
      task_id: "task-1",
      objective: "Show bounded Git evidence.",
      readable_files: ["src/**"],
      writable_files: ["src/app.py"],
      readonly_files: ["tests/**"],
      acceptance_criteria: ["Diff is read-only."],
      verification_commands: ["pytest -q"],
      max_retries: 1,
    },
    contract_sha256: "c".repeat(64),
    created_at: "2026-08-19T00:00:00Z",
    evidence: [],
  });
  vi.mocked(productApi.getTaskDiff).mockImplementation(async (_run, _task, kind) =>
    diff(kind ?? "TASK"),
  );
});

describe("TaskDetailPage diff viewer", () => {
  it("renders backend-resolved task diff without Git mutation controls", async () => {
    renderTask();

    expect(await screen.findByText(/WORKER_EXECUTION evidence #7/)).toBeInTheDocument();
    expect(screen.getByText("src/app.py")).toBeInTheDocument();
    expect(screen.getByText("+new = 2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stage/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /commit/i })).not.toBeInTheDocument();
  });

  it("switches evidence kind while leaving commit selection to the backend", async () => {
    renderTask();
    await screen.findByText(/WORKER_EXECUTION evidence #7/);

    fireEvent.click(screen.getByRole("button", { name: "Integration changes" }));

    await waitFor(() =>
      expect(productApi.getTaskDiff).toHaveBeenLastCalledWith(
        runId,
        "task-1",
        "INTEGRATION",
      ),
    );
    expect(await screen.findByText(/MERGE_QUEUE_SNAPSHOT evidence #9/)).toBeInTheDocument();
  });
});