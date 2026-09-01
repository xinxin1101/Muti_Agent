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

function recoveryPlan(
  frontierState: productApi.OperatorRecoveryTask["frontier_state"] = "WAIT_ACTIVE_OWNER",
): productApi.OperatorRecoveryPlan {
  return {
    run_id: runId,
    diagnostic_only: true,
    mutation_requires_fresh_revalidation: true,
    reconciliation: {
      run_id: runId,
      run_status: "RUNNING",
      dag_sha256: "d".repeat(64),
      topology_source: "PERSISTED",
      observed_at: "2026-08-19T00:00:00Z",
      topological_order: ["task-1"],
      completed_task_ids: [],
      failed_task_ids: [],
      blocked_task_ids: [],
      ready_task_ids: ["task-1"],
      reconcile_task_ids: ["task-1"],
      tasks: [{
        run_id: runId,
        task_id: "task-1",
        depends_on: [],
        topological_index: 0,
        frontier_state: frontierState,
        lease_state: frontierState === "BLOCKED_RECOVERY_GAP" ? "RELEASED" : "ACTIVE",
        lease_generation: 1,
        lease_dispatch_id: "33333333-3333-3333-3333-333333333333",
        worker_execution_status: null,
        worker_execution_evidence_id: null,
        reason: "test recovery state",
      }],
    },
    actions: [],
  };
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
    token_budget: {
      total_budget_tokens: 30000,
      used_prompt_tokens: 0,
      used_completion_tokens: 0,
      used_total_tokens: 0,
      reserved_tokens: 0,
      status: "NORMAL",
      roles: [],
    },
    performance: {
      developer_model_latency_ms: 0,
      repair_model_latency_ms: 0,
      repository_tool_latency_ms: 0,
      verification_latency_ms: 0,
      context_estimated_tokens: 0,
      context_reused_files: 0,
      context_trimmed_files: 0,
    },
    workflow: {
      activation_mode: "workflow_first",
      workflow_tasks: 0,
      agent_tasks: 0,
      hybrid_tasks: 1,
      workflow_calls: 0,
      agent_calls: 1,
      workflow_duration_ms: 0,
      estimated_tokens_saved: 0,
      agent_escalations: 0,
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
  vi.mocked(productApi.getOperatorRecovery).mockResolvedValue(recoveryPlan());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RunDashboardPage live timeline", () => {
  it("presents a released lease without terminal evidence as recovery required", async () => {
    vi.mocked(productApi.getOperatorRecovery).mockResolvedValue(
      recoveryPlan("BLOCKED_RECOVERY_GAP"),
    );
    vi.mocked(productApi.recoverInterruptedRun).mockResolvedValue({
      run_id: "99999999-9999-9999-9999-999999999999",
      project_id: "11111111-1111-1111-1111-111111111111",
      base_commit: "a".repeat(40),
      dag_sha256: "d".repeat(64),
      task_ids: ["task-1"],
      initial_ready_task_ids: ["task-1"],
      launch_state: "QUEUED",
      dispatches: [{
        task_id: "task-1",
        state: "QUEUED",
        dispatch_id: "33333333-3333-3333-3333-333333333333",
        broker_message_id: "message-1",
        queue_name: "devflow_tasks",
        detail: null,
      }],
    });

    renderDashboard();

    expect(await screen.findByRole("region", { name: "运行进展" })).toBeInTheDocument();
    expect(screen.getByText(/该运行已异常中断/)).toBeInTheDocument();
    expect(screen.getByText(/租约心跳仅说明 Worker/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建恢复运行" }));
    await waitFor(() => expect(productApi.recoverInterruptedRun).toHaveBeenCalledWith(runId));
  });

  it("renders descriptive Run Metrics without a browser success score", async () => {
    renderDashboard();

    expect(await screen.findByRole("region", { name: "运行指标" })).toBeInTheDocument();
    expect(screen.getByText("执行路径")).toBeInTheDocument();
    expect(screen.getByText("Hybrid 任务")).toBeInTheDocument();
    expect(screen.getByText("验证尝试")).toBeInTheDocument();
    expect(screen.getByText("审查结论")).toBeInTheDocument();
    expect(screen.getByText(/状态仍来自 PERSISTED_RUN/)).toBeInTheDocument();
    expect(screen.queryByText(/success rate/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/approval rate/i)).not.toBeInTheDocument();
  });

  it("renders the backend-validated DAG with task navigation", async () => {
    renderDashboard();

    expect(
      await screen.findByRole("img", { name: "已验证的任务依赖 DAG" }),
    ).toBeInTheDocument();
    const taskLink = screen.getByRole("link", { name: "打开任务 task-1" });
    expect(taskLink).toHaveAttribute("href", `/runs/${runId}/tasks/task-1`);
    expect(screen.getByText(/拓扑来源：已持久化/)).toBeInTheDocument();
  });

  it("shows accepted failure evidence and retries as a new server-owned Run", async () => {
    vi.mocked(productApi.getRun).mockResolvedValue({
      run_id: runId,
      project_id: "11111111-1111-1111-1111-111111111111",
      repository_url: "https://github.com/example/repo",
      default_branch: "main",
      status: "FAILED",
      base_commit: "a".repeat(40),
      task_count: 1,
      started_at: "2026-08-19T00:00:00Z",
      finished_at: "2026-08-19T00:01:00Z",
      tasks: [],
      failures: [{
        task_id: "task-1",
        failure_type: "TOOL_FAILURE",
        source: "verification",
        message: "Python 编译缓存无法写入只读工作目录。",
        retryable: false,
        evidence: ["command=python3 -m py_compile hello.py", "stderr=Read-only file system: '__pycache__'"],
      }],
    });
    vi.mocked(productApi.retryRun).mockResolvedValue({
      run_id: "99999999-9999-9999-9999-999999999999",
      project_id: "11111111-1111-1111-1111-111111111111",
      base_commit: "a".repeat(40),
      dag_sha256: "d".repeat(64),
      task_ids: ["task-1"],
      initial_ready_task_ids: ["task-1"],
      launch_state: "QUEUED",
      dispatches: [{
        task_id: "task-1",
        state: "QUEUED",
        dispatch_id: "33333333-3333-3333-3333-333333333333",
        broker_message_id: "message-1",
        queue_name: "devflow_tasks",
        detail: null,
      }],
    });
    vi.mocked(productApi.explainRunFailure).mockResolvedValue({
      run_id: runId,
      failure_fingerprint: "e".repeat(64),
      explanation: "验证容器将工作目录设为只读，Python 无法创建编译缓存。请修复验证环境后重新运行。",
      model: "zai-org/GLM-5.2",
      cached: false,
      created_at: "2026-08-19T00:01:00Z",
    });

    renderDashboard();

    expect(await screen.findByRole("region", { name: "失败原因" })).toBeInTheDocument();
    expect(screen.getByText(/Python 编译缓存无法写入/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only file system/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "AI 解读失败原因" }));
    expect(await screen.findByText(/验证容器将工作目录设为只读/)).toBeInTheDocument();
    expect(productApi.explainRunFailure).toHaveBeenCalledWith(runId);
    fireEvent.click(screen.getByRole("button", { name: "重新发起运行" }));
    await waitFor(() => expect(productApi.retryRun).toHaveBeenCalledWith(runId));
  });

  it("previews reusable work before continuing development", async () => {
    vi.mocked(productApi.getRun).mockResolvedValue({
      run_id: runId,
      project_id: "11111111-1111-1111-1111-111111111111",
      repository_url: "https://github.com/example/repo",
      default_branch: "main",
      status: "FAILED",
      base_commit: "a".repeat(40),
      development_session_id: "33333333-3333-3333-3333-333333333333",
      task_count: 2,
      started_at: "2026-08-19T00:00:00Z",
      finished_at: "2026-08-19T00:01:00Z",
      tasks: [],
      failures: [],
    });
    vi.mocked(productApi.getDevelopmentSessionRecovery).mockResolvedValue({
      session_id: "33333333-3333-3333-3333-333333333333",
      source_run_id: runId,
      baseline_commit: "a".repeat(40),
      current_commit: "a".repeat(40),
      baseline_state: "UNCHANGED",
      reusable_work_package_ids: ["core"],
      checkpointed_work_package_ids: ["web"],
      remaining_work_package_ids: ["web"],
      next_action: "继续未完成工作包",
      budget: {
        planning_remaining_tokens: 4000,
        development_remaining_tokens: 6000,
        repair_remaining_tokens: 2000,
        estimated_new_development_tokens: 5000,
        estimated_tokens_saved: 6000,
      },
    });
    vi.mocked(productApi.continueDevelopmentSession).mockResolvedValue({
      run_id: "99999999-9999-9999-9999-999999999999",
      project_id: "11111111-1111-1111-1111-111111111111",
      base_commit: "a".repeat(40),
      dag_sha256: "d".repeat(64),
      task_ids: ["web"],
      initial_ready_task_ids: ["web"],
      launch_state: "QUEUED",
      dispatches: [{ task_id: "web", state: "QUEUED", dispatch_id: null, broker_message_id: null, queue_name: null, detail: null }],
    });

    renderDashboard();

    expect(await screen.findByText("继续开发预览")).toBeInTheDocument();
    expect(screen.getByText(/已复用 1 个工作包/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "继续开发" }));
    await waitFor(() => expect(productApi.continueDevelopmentSession).toHaveBeenCalledWith("33333333-3333-3333-3333-333333333333", "AUTO"));
  });

  it("labels a controlled Developer time limit without calling it a tool failure", async () => {
    vi.mocked(productApi.getRun).mockResolvedValue({
      run_id: runId,
      project_id: "11111111-1111-1111-1111-111111111111",
      repository_url: "https://github.com/example/repo",
      default_branch: "main",
      status: "FAILED",
      base_commit: "a".repeat(40),
      task_count: 1,
      started_at: "2026-08-19T00:00:00Z",
      finished_at: "2026-08-19T00:01:00Z",
      tasks: [],
      failures: [{
        task_id: "ai-engine",
        // Legacy persisted records used TOOL_FAILURE with this structured stop reason. The UI
        // must keep displaying their real meaning after the backend introduces AGENT_TIME_LIMIT.
        failure_type: "TOOL_FAILURE",
        source: "runtime",
        message: "开发智能体时间预算耗尽，未能在限制内完成代码修改。",
        retryable: false,
        evidence: [
          "stop_reason=TIME_LIMIT",
          "developer_max_duration_seconds=600",
          "developer_max_model_turn_seconds=180",
        ],
      }],
    });

    renderDashboard();

    const panel = await screen.findByRole("region", { name: "失败原因" });
    expect(panel).toHaveTextContent("开发智能体时间预算耗尽");
    expect(panel).toHaveTextContent("developer_max_duration_seconds=600");
    expect(panel).not.toHaveTextContent("运行环境或工具故障");
  });

  it("renders a durable conflict gate and sends only decision evidence", async () => {
    const pendingGate = humanGate();
    const authorizedGate = humanGate("REPAIR_AUTHORIZED");
    vi.mocked(productApi.listHumanGates).mockResolvedValue([pendingGate]);
    vi.mocked(productApi.decideHumanGate).mockResolvedValue(authorizedGate);

    renderDashboard();

    expect(
      await screen.findByRole("region", { name: "持久化人工门控" }),
    ).toBeInTheDocument();
    expect(screen.getByText("app/auth.py")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("任务 task-1 的决策备注"), {
      target: { value: "Reviewed conflict scope" },
    });
    fireEvent.click(screen.getByRole("button", { name: "授权受限修复" }));

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
      await screen.findByRole("heading", { name: "运行看板" }),
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

    expect(await screen.findByText("运行已启动")).toBeInTheDocument();
    expect(screen.getByText("运行记录已创建，正在等待任务调度。")).toBeInTheDocument();
    expect(screen.getByText("实时连接 · #1")).toBeInTheDocument();

    rendered.unmount();
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it("re-reads Metrics and DAG projections after accepted events arrive", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "运行看板" });
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
    await screen.findByRole("heading", { name: "运行看板" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(2, "44444444-4444-4444-4444-444444444444"),
      );
    });

    expect(
      await screen.findByText(/运行时事件流未从序列 1 开始/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances[0]?.closed).toBe(true),
    );
  });

  it("allows an exact duplicate but rejects sequence reuse with a different event id", async () => {
    renderDashboard();
    await screen.findByRole("heading", { name: "运行看板" });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));

    const first = runtimeEvent(1, "33333333-3333-3333-3333-333333333333");
    act(() => {
      FakeEventSource.instances[0]?.message(first);
      FakeEventSource.instances[0]?.message(first);
    });

    expect(await screen.findByText("运行已启动")).toBeInTheDocument();
    expect(screen.getAllByText("运行已启动")).toHaveLength(1);

    act(() => {
      FakeEventSource.instances[0]?.message(
        runtimeEvent(1, "55555555-5555-5555-5555-555555555555"),
      );
    });

    expect(
      await screen.findByText(/运行时事件序列发生倒退，或被另一事件重复使用/),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(FakeEventSource.instances[0]?.closed).toBe(true),
    );
  });
});
