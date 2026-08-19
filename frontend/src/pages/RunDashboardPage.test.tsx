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
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RunDashboardPage live timeline", () => {
  it("subscribes through EventSource and renders accepted runtime events", async () => {
    const rendered = renderDashboard();

    expect(
      await screen.findByRole("heading", { name: "Run Dashboard" }),
    ).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0]?.url).toContain(
      `/api/v1/runs/${runId}/events`,
    );

    act(() => {
      FakeEventSource.instances[0]?.open();
      FakeEventSource.instances[0]?.message(
        JSON.stringify({
          id: 1,
          event_id: "33333333-3333-3333-3333-333333333333",
          run_id: runId,
          sequence: 1,
          event_key: "run:started",
          kind: "RUN_STARTED",
          source: "PERSISTENCE",
          level: "INFO",
          task_id: null,
          dispatch_id: null,
          generation: null,
          message: "Persisted run started.",
          schema_version: 1,
          attributes: {
            project_id: "11111111-1111-1111-1111-111111111111",
          },
          attributes_sha256: "b".repeat(64),
          created_at: "2026-08-19T00:00:00Z",
        }),
      );
    });

    expect(await screen.findByText("RUN_STARTED")).toBeInTheDocument();
    expect(screen.getByText("Persisted run started.")).toBeInTheDocument();
    expect(screen.getByText("LIVE · #1")).toBeInTheDocument();

    rendered.unmount();
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it("fails closed on a sequence gap instead of presenting corrupted order", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "Run Dashboard" });

    act(() => {
      FakeEventSource.instances[0]?.message(
        JSON.stringify({
          id: 2,
          event_id: "44444444-4444-4444-4444-444444444444",
          run_id: runId,
          sequence: 2,
          event_key: "event:2",
          kind: "EVIDENCE_RECORDED",
          source: "RUNTIME",
          level: "INFO",
          task_id: "task-1",
          dispatch_id: null,
          generation: null,
          message: "Accepted evidence.",
          schema_version: 1,
          attributes: {},
          attributes_sha256: "c".repeat(64),
          created_at: "2026-08-19T00:00:01Z",
        }),
      );
    });

    expect(
      await screen.findByText(/did not start at sequence 1/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances[0]?.closed).toBe(true),
    );
  });
});
