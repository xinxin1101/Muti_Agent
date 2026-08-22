import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function humanGate(state: productApi.IntegrationGateStateName = "AWAITING_HUMAN") {
  const recordedDecision: productApi.HumanGateDecisionName =
    state === "ABORTED" ? "ABORT" : "AUTHORIZE_REPAIR";
  return {
    task_id: "task-1",
    task_branch: "devflow/task/task-1",
    task_commit: "c".repeat(40),
    integration_ref: "refs/devflow/integration/run-test",
    integration_head: "d".repeat(40),
    conflict_ref: "refs/devflow/integration-conflicts/run-test",
    conflict_marker_commit: "e".repeat(40),
    evidence_fingerprint: "f".repeat(64),
    policy_fingerprint: "a".repeat(64),
    policy: {
      route: "HUMAN_REQUIRED" as const,
      evidence_fingerprint: "f".repeat(64),
      automatic_repair_enabled: false,
      human_repair_authorizable: true,
      conflicting_paths: ["app/auth.py"],
      reasons: ["automatic merge-conflict repair policy is disabled"],
    },
    state,
    human_decision:
      state === "AWAITING_HUMAN"
        ? null
        : {
            decision: recordedDecision,
            actor: "product-user",
            note: "",
            decision_ref: "refs/devflow/integration-decisions/run-test",
            decision_commit: "1".repeat(40),
            evidence_fingerprint: "f".repeat(64),
            policy_fingerprint: "a".repeat(64),
            conflict_marker_commit: "e".repeat(40),
          },
    repair_may_start: state === "REPAIR_AUTHORIZED",
    integration_may_advance: false as const,
  } satisfies productApi.HumanGateSnapshot;
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
  vi.mocked(productApi.getRunMetrics).mockResolvedValue({
    run_id: runId,
    project_id: "11111111-1111-1111-1111-111111111111",
    status: "RUNNING",
    status_basis: "PERSISTED_RUN",
    task_count: 1,
    started_at: "2026-08-19T00:00:00Z",
    finished_at: null,
    terminal_duration_ms: null,
    evidence: {
      total_records: 8,
      developer_runs: 1,
      verification_attempts: 3,
      review_decisions: 1,
      repair_attempts: 1,
      failure_reports: 1,
      dispatch_events: 1,
      worker_executions: 1,
      merge_queue_snapshots: 0,
      merge_conflicts: 0,
      integration_gate_evaluations: 0,
      human_decisions: 0,
    },
    runtime_events: {
      total_events: 4,
      warning_events: 1,
      error_events: 0,
      lease_acquisitions: 1,
      lease_takeovers: 0,
      lease_releases: 0,
      latest_sequence: 4,
    },
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
  vi.mocked(productApi.listHumanGates).mockResolvedValue([]);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RunDashboardPage live timeline", () => {
  it("renders descriptive Run Metrics without a browser success score", async () => {
    renderDashboard();

    expect(await screen.findByRole("region", { name: "Run metrics" })).toBeInTheDocument();
    expect(screen.getByText("Verification attempts")).toBeInTheDocument();
    expect(screen.getByText("Review decisions")).toBeInTheDocument();
    expect(screen.getByText(/Status remains sourced from PERSISTED_RUN/)).toBeInTheDocument();
    expect(screen.queryByText(/success rate/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/approval rate/i)).not.toBeInTheDocument();
  });

  it("renders the backend-validated DAG with task navigation", async () => {
    renderDashboard();

    expect(
      await screen.findByRole("img", { name: "Validated task dependency DAG" }),
    ).toBeInTheDocument();
    const taskLink = screen.getByRole("link", { name: "Open task task-1" });
    expect(taskLink).toHaveAttribute("href", `/runs/${runId}/tasks/task-1`);
    expect(screen.getByText(/Topology: PERSISTED/)).toBeInTheDocument();
  });

  it("renders a durable conflict gate and sends only decision evidence", async () => {
    const pendingGate = humanGate();
    const authorizedGate = humanGate("REPAIR_AUTHORIZED");
    vi.mocked(productApi.listHumanGates).mockResolvedValue([pendingGate]);
    vi.mocked(productApi.decideHumanGate).mockResolvedValue(authorizedGate);

    renderDashboard();

    expect(
      await screen.findByRole("region", { name: "Durable human gates" }),
    ).toBeInTheDocument();
    expect(screen.getByText("app/auth.py")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Decision note for task-1"), {
      target: { value: "Reviewed conflict scope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Authorize bounded repair" }));

    await waitFor(() =>
      expect(productApi.decideHumanGate).toHaveBeenCalledWith(runId, "task-1", {
        evidence_fingerprint: "f".repeat(64),
        decision: "AUTHORIZE_REPAIR",
        note: "Reviewed conflict scope",
      }),
    );
    const payload = vi.mocked(productApi.decideHumanGate).mock.calls[0]?.[2];
    expect(payload).not.toHaveProperty("task_commit");
    expect(payload).not.toHaveProperty("run_token");
    expect(payload).not.toHaveProperty("branch");
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

  it("re-reads Metrics and DAG projections after accepted events arrive", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Run Dashboard" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    await waitFor(() => expect(productApi.getRunDAG).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(productApi.getRunMetrics).toHaveBeenCalledTimes(1));

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(1, "33333333-3333-3333-3333-333333333333"),
      );
      FakeEventSource.instances[0]?.message(
        runtimeEvent(2, "44444444-4444-4444-4444-444444444444"),
      );
    });

    await waitFor(() => expect(productApi.getRunDAG).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(vi.mocked(productApi.getRunMetrics).mock.calls.length).toBeGreaterThanOrEqual(2),
    );
    await waitFor(() => expect(productApi.listHumanGates).toHaveBeenCalledTimes(2));
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
