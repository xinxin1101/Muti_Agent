import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { RunDashboardPage } from "./RunDashboardPage";

vi.mock("../api/product");

const runId = "22222222-2222-2222-2222-222222222222";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.(new Event("open"));
  }

  message(data: string) {
    this.onmessage?.(new MessageEvent("message", { data }));
  }
}

function runtimeEvent(
  sequence: number,
  eventId: string,
  overrides: Record<string, unknown> = {},
) {
  return JSON.stringify({
    id: sequence,
    event_id: eventId,
    run_id: runId,
    sequence,
    event_key: `event:${sequence}`,
    kind: sequence === 1 ? "RUN_STARTED" : "EVIDENCE_RECORDED",
    source: sequence === 1 ? "PERSISTENCE" : "RUNTIME",
    level: "INFO",
    task_id: sequence === 1 ? null : "task-1",
    dispatch_id: null,
    generation: null,
    message: sequence === 1 ? "Persisted run started." : "Accepted evidence.",
    schema_version: 1,
    attributes: {},
    attributes_sha256: "b".repeat(64),
    created_at: "2026-08-19T00:00:00Z",
    ...overrides,
  });
}

function renderDashboard() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/runs/${runId}`]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDashboardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  vi.stubGlobal(
    "EventSource",
    FakeEventSource as unknown as typeof EventSource,
  );
  vi.mocked(productApi.getRun).mockResolvedValue({
    run_id: runId,
    project_id: "11111111-1111-1111-1111-111111111111",
    repository_url: "https://github.com/example/repo",
    default_branch: "main",
    status: "RUNNING",
    base_commit: "a".repeat(40),
    task_count: 1,
    started_at: "2026-08-19T00:00:00Z",
    finished_at: null,
    tasks: [
      {
        task_id: "task-1",
        objective: "Stream accepted runtime events.",
        evidence_count: 0,
      },
    ],
  });
  vi.mocked(productApi.getRunDAG).mockResolvedValue({
    run_id: runId,
    dag_sha256: "d".repeat(64),
    topology_source: "PERSISTED",
    topological_order: ["task-1"],
    nodes: [
      {
        task_id: "task-1",
        objective: "Stream accepted runtime events.",
        depends_on: [],
        topological_index: 0,
        layer: 0,
        presentation_state: "READY",
        state_basis: "DERIVED_DAG",
      },
    ],
    edges: [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RunDashboardPage live timeline", () => {
  it("renders the backend-validated DAG with task navigation", async () => {
    renderDashboard();

    expect(
      await screen.findByRole("img", { name: "Validated task dependency DAG" }),
    ).toBeInTheDocument();
    const taskLink = screen.getByRole("link", { name: "Open task task-1" });
    expect(taskLink).toHaveAttribute("href", `/runs/${runId}/tasks/task-1`);
    expect(screen.getByText(/Topology: PERSISTED/)).toBeInTheDocument();
  });

  it("subscribes after REST confirms the Run and renders accepted events", async () => {
    const rendered = renderDashboard();

    expect(FakeEventSource.instances).toHaveLength(0);
    expect(
      await screen.findByRole("heading", { name: "Run Dashboard" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0]?.url).toContain(
      `/api/v1/runs/${runId}/events`,
    );

    act(() => {
      FakeEventSource.instances[0]?.open();
      FakeEventSource.instances[0]?.message(
        runtimeEvent(1, "33333333-3333-3333-3333-333333333333"),
      );
    });

    expect(await screen.findByText("RUN_STARTED")).toBeInTheDocument();
    expect(screen.getByText("Persisted run started.")).toBeInTheDocument();
    expect(screen.getByText("LIVE · #1")).toBeInTheDocument();

    rendered.unmount();
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it("re-reads DAG presentation state after accepted evidence arrives", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Run Dashboard" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(productApi.getRunDAG).toHaveBeenCalledTimes(1));

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(1, "33333333-3333-3333-3333-333333333333"),
      );
      FakeEventSource.instances[0]?.message(
        runtimeEvent(2, "44444444-4444-4444-4444-444444444444"),
      );
    });

    await waitFor(() => expect(productApi.getRunDAG).toHaveBeenCalledTimes(2));
  });

  it("fails closed on a sequence gap instead of presenting corrupted order", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Run Dashboard" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(2, "44444444-4444-4444-4444-444444444444"),
      );
    });

    expect(
      await screen.findByText(/did not start at sequence 1/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances[0]?.closed).toBe(true),
    );
  });

  it("allows an exact duplicate but rejects sequence reuse with a different event id", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Run Dashboard" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    const first = runtimeEvent(1, "33333333-3333-3333-3333-333333333333");
    act(() => {
      FakeEventSource.instances[0]?.message(first);
      FakeEventSource.instances[0]?.message(first);
    });

    expect(await screen.findByText("RUN_STARTED")).toBeInTheDocument();
    expect(screen.getAllByText("RUN_STARTED")).toHaveLength(1);

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(1, "55555555-5555-5555-5555-555555555555"),
      );
    });

    expect(
      await screen.findByText(/moved backward or was reused/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances[0]?.closed).toBe(true),
    );
  });
});
