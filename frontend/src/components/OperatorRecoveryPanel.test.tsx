import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { OperatorRecoveryPanel } from "./OperatorRecoveryPanel";

vi.mock("../api/product");

const runId = "22222222-2222-2222-2222-222222222222";
const actionId = "a".repeat(64);

function recoveryPlan(
  actions: readonly productApi.OperatorAction[] = [
    {
      action_id: actionId,
      kind: "ADVANCE_RUN",
      label: "Advance from durable facts",
      description: "Fresh server-side revalidation is required.",
    },
  ],
): productApi.OperatorRecoveryPlan {
  return {
    run_id: runId,
    diagnostic_only: true,
    mutation_requires_fresh_revalidation: true,
    reconciliation: {
      run_id: runId,
      run_status: "RUNNING",
      dag_sha256: "b".repeat(64),
      topology_source: "PERSISTED",
      observed_at: "2026-08-22T06:00:00Z",
      topological_order: ["task-1"],
      completed_task_ids: [],
      failed_task_ids: [],
      blocked_task_ids: [],
      ready_task_ids: ["task-1"],
      reconcile_task_ids: ["task-1"],
      tasks: [
        {
          run_id: runId,
          task_id: "task-1",
          depends_on: [],
          topological_index: 0,
          frontier_state: "RECONCILE_CANDIDATE",
          lease_state: "EXPIRED",
          lease_generation: 2,
          lease_dispatch_id: "33333333-3333-3333-3333-333333333333",
          worker_execution_status: null,
          worker_execution_evidence_id: null,
          reason: "Fresh Step 5.3 revalidation is required before publication.",
        },
      ],
    },
    actions,
  };
}

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OperatorRecoveryPanel runId={runId} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.getOperatorRecovery).mockResolvedValue(recoveryPlan());
  vi.mocked(productApi.executeOperatorAction).mockResolvedValue({
    run_id: runId,
    action: recoveryPlan().actions[0]!,
    request_evidence_id: 17,
    execution_delegated: true,
    refreshed_plan: recoveryPlan([]),
  });
});

describe("OperatorRecoveryPanel", () => {
  it("renders server-advertised durable state and sends only the opaque action id", async () => {
    renderPanel();

    expect(
      await screen.findByRole("region", { name: "Operator recovery" }),
    ).toBeInTheDocument();
    expect(screen.getByText("RECONCILE_CANDIDATE")).toBeInTheDocument();
    expect(screen.getByText(/Causal trace can explain/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Advance from durable facts" }));

    await waitFor(() =>
      expect(productApi.executeOperatorAction).toHaveBeenCalledWith(runId, actionId),
    );
    expect(vi.mocked(productApi.executeOperatorAction).mock.calls[0]).toHaveLength(2);
  });

  it("does not synthesize a recovery button when the server advertises no action", async () => {
    vi.mocked(productApi.getOperatorRecovery).mockResolvedValue(recoveryPlan([]));

    renderPanel();

    expect(
      await screen.findByText(/No operator mutation is currently advertised by the server/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
