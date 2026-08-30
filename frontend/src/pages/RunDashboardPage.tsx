import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";

import {
  getRun,
  getRunDAG,
  getRunMetrics,
  explainRunFailure,
  getDependencyEnvironment,
  resumeRun,
  retryRun,
  type RequirementRunLaunchResponse,
} from "../api/product";
import {
  parseRuntimeEventSummary,
  runtimeEventStreamUrl,
} from "../api/runtime-events";
import { GitHubPublication } from "../components/GitHubPublication";
import { DependencyEnvironmentPanel } from "../components/DependencyEnvironmentPanel";
import { HumanGatePanel } from "../components/HumanGatePanel";
import { OperatorRecoveryPanel } from "../components/OperatorRecoveryPanel";
import { RunDAG } from "../components/RunDAG";
import { RunMetrics } from "../components/RunMetrics";
import { RunProgressPanel } from "../components/RunProgressPanel";
import {
  formatDateTime,
  labelFor,
  translateRuntimeEventMessage,
  translateTaskObjective,
} from "../i18n";
import type {
  ProductFailureExplanation,
  ProductDAGNode,
  ProductRunDetail,
  ProductRunMetrics,
  ProductRunCheckpoint,
  ProductRunFailure,
  RunLaunchResponse,
} from "../types/product";
import type { RuntimeEventSummary } from "../types/runtime";

type LaunchState = Readonly<{
  launch?: RunLaunchResponse | RequirementRunLaunchResponse;
}>;

type StreamStatus =
  | "connecting"
  | "live"
  | "reconnecting"
  | "unsupported"
  | "error";

const MAX_TIMELINE_EVENTS = 500;

export function RunDashboardPage() {
  const { runId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const launch = (location.state as LaunchState | null)?.launch;
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    enabled: Boolean(runId),
  });
  const metrics = useQuery({
    queryKey: ["run-metrics", runId],
    queryFn: () => getRunMetrics(runId),
    enabled: Boolean(runId) && run.isSuccess,
  });
  const dag = useQuery({
    queryKey: ["run-dag", runId],
    queryFn: () => getRunDAG(runId),
    enabled: Boolean(runId) && run.isSuccess,
  });
  const dependencyEnvironment = useQuery({
    queryKey: ["dependency-environment", run.data?.project_id],
    queryFn: () => getDependencyEnvironment(run.data!.project_id),
    enabled: run.isSuccess,
  });
  const retry = useMutation({
    mutationFn: () => retryRun(runId),
    onSuccess: (nextLaunch) => {
      void navigate(`/runs/${nextLaunch.run_id}`, { state: { launch: nextLaunch } });
    },
  });
  const retryCachedEnvironment = useMutation({
    mutationFn: () => retryRun(runId),
    onSuccess: (nextLaunch) => {
      void navigate(`/runs/${nextLaunch.run_id}`, { state: { launch: nextLaunch } });
    },
  });
  const resume = useMutation({
    mutationFn: () => resumeRun(runId),
    onSuccess: (nextLaunch) => {
      void navigate(`/runs/${nextLaunch.run_id}`, { state: { launch: nextLaunch } });
    },
  });
  const explanation = useMutation({
    mutationFn: () => explainRunFailure(runId),
  });
  const [events, setEvents] = useState<readonly RuntimeEventSummary[]>([]);
  const [streamStatus, setStreamStatus] =
    useState<StreamStatus>("connecting");
  const [streamError, setStreamError] = useState<string | null>(null);
  const [activeDetail, setActiveDetail] = useState<"activity" | "dag" | "context" | null>(null);
  const lastSequenceRef = useRef(0);
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    setEvents([]);
    setStreamError(null);
    setStreamStatus("connecting");
    lastSequenceRef.current = 0;
    lastEventIdRef.current = null;

    if (!runId || !run.isSuccess) {
      return;
    }
    if (typeof EventSource === "undefined") {
      setStreamStatus("unsupported");
      return;
    }

    const source = new EventSource(runtimeEventStreamUrl(runId));

    source.onopen = () => {
      setStreamStatus("live");
      setStreamError(null);
    };

    source.onmessage = (message) => {
      try {
        const event = parseRuntimeEventSummary(message.data);
        if (event.run_id !== runId) {
          throw new Error("运行时事件属于其他运行记录。");
        }

        if (event.sequence <= lastSequenceRef.current) {
          if (
            event.sequence === lastSequenceRef.current &&
            event.event_id === lastEventIdRef.current
          ) {
            return;
          }
          throw new Error(
            "运行时事件序列发生倒退，或被另一事件重复使用。",
          );
        }
        if (
          lastSequenceRef.current > 0 &&
          event.sequence !== lastSequenceRef.current + 1
        ) {
          throw new Error("运行时事件序列出现意外缺口。");
        }
        if (lastSequenceRef.current === 0 && event.sequence !== 1) {
          throw new Error("运行时事件流未从序列 1 开始。");
        }

        lastSequenceRef.current = event.sequence;
        lastEventIdRef.current = event.event_id;
        setEvents((current) =>
          [...current, event].slice(-MAX_TIMELINE_EVENTS),
        );
        setStreamStatus("live");
        setStreamError(null);

        void queryClient.invalidateQueries({ queryKey: ["run-metrics", runId] });
        if (
          event.kind === "EVIDENCE_RECORDED" ||
          event.kind === "LEASE_RELEASED" ||
          event.kind === "RUN_FINALIZED"
        ) {
          void queryClient.invalidateQueries({ queryKey: ["run", runId] });
          void queryClient.invalidateQueries({ queryKey: ["run-dag", runId] });
          void queryClient.invalidateQueries({ queryKey: ["human-gates", runId] });
          void queryClient.invalidateQueries({ queryKey: ["operator-recovery", runId] });
        }
      } catch (error) {
        source.close();
        setStreamStatus("error");
        setStreamError(
          error instanceof Error
            ? error.message
            : "运行时事件流验证失败。",
        );
      }
    };

    source.onerror = () => {
      setStreamStatus((current) =>
        current === "error" ? current : "reconnecting",
      );
    };

    return () => {
      source.close();
    };
  }, [queryClient, run.isSuccess, runId]);

  if (run.isLoading) {
    return <p className="text-slate-400">正在加载运行记录…</p>;
  }
  if (run.error || !run.data) {
    return <p className="text-rose-300">{run.error?.message ?? "未找到运行记录。"}</p>;
  }

  const nodes = dag.data?.nodes ?? [];
  const currentTask = nodes.find((node) => ["RUNNING", "VERIFYING", "REPAIRING", "REVIEWING"].includes(node.presentation_state)) ?? nodes.find((node) => node.presentation_state === "READY") ?? null;
  const completedTaskCount = nodes.length
    ? nodes.filter((node) => node.presentation_state === "SUCCEEDED").length
    : run.data.status === "SUCCEEDED"
      ? run.data.task_count
      : 0;

  return (
    <section className="mx-auto max-w-[1500px] space-y-6">
      <RunHero
        run={run.data}
        currentTask={currentTask}
        metrics={metrics.data ?? null}
        nodes={nodes}
        completedTaskCount={completedTaskCount}
        latestEvent={events.at(-1) ?? null}
        onOpenActivity={() => setActiveDetail("activity")}
        onOpenDag={() => setActiveDetail("dag")}
      />

      {launch ? <LaunchNotice launch={launch} /> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="min-w-0 space-y-6">
          <TaskRail runId={runId} nodes={nodes} />

          <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-stone-900">正在进行</p>
                <h2 className="mt-2 text-lg font-semibold text-stone-800">{currentTask ? translateTaskObjective(currentTask.objective) : run.data.status === "SUCCEEDED" ? "本次开发已完成" : "正在等待下一步"}</h2>
                <p className="mt-2 text-sm text-stone-500">{events.at(-1) ? `最近动作：${translateRuntimeEventMessage(events.at(-1)!.kind, events.at(-1)!.message)}` : "系统正在等待首条有效运行事件。"}</p>
              </div>
              <button type="button" onClick={() => setActiveDetail("activity")} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 hover:bg-stone-50">查看实时过程</button>
            </div>
          </section>

          {run.data.status === "FAILED" ? (
            <FailureSummary
          failures={run.data.failures ?? []}
          checkpoint={run.data.checkpoint ?? null}
          explanation={explanation.data}
          explanationError={
            explanation.error instanceof Error ? explanation.error.message : null
          }
          explaining={explanation.isPending}
          onExplain={() => explanation.mutate()}
          retryError={retry.error instanceof Error ? retry.error.message : null}
          retrying={retry.isPending}
          onRetry={() => retry.mutate()}
          cacheReady={dependencyEnvironment.data?.cache_state === "HIT" || dependencyEnvironment.data?.cache_state === "NOT_REQUIRED"}
          cachedRetrying={retryCachedEnvironment.isPending}
          cachedRetryError={retryCachedEnvironment.error instanceof Error ? retryCachedEnvironment.error.message : null}
          onCachedRetry={() => retryCachedEnvironment.mutate()}
          resumeError={resume.error instanceof Error ? resume.error.message : null}
          resuming={resume.isPending}
          onResume={() => resume.mutate()}
            />
          ) : null}

          <details className="rounded-2xl border border-stone-200 bg-white p-5" open={activeDetail === "activity"} onToggle={(event) => !event.currentTarget.open && setActiveDetail(null)}>
            <summary className="cursor-pointer text-sm font-medium text-stone-700">实时过程与原始事件</summary>
            <div className="mt-5 space-y-3">

      <div className="space-y-3">
        <Metric label="基线提交" value={run.data.base_commit.slice(0, 12)} mono />
        <Metric label="任务数" value={String(run.data.task_count)} />
        <Metric label="启动时间" value={formatDateTime(run.data.started_at)} />
      </div>

      <ActivityFeed events={events} streamStatus={streamStatus} />
      <div className="pt-4"><RunProgressPanel run={run.data} events={events} onRecovered={(nextRunId, nextLaunch) => { void navigate(`/runs/${nextRunId}`, { state: { launch: nextLaunch } }); }} /></div>
            </div>
          </details>

          <details className="rounded-2xl border border-stone-200 bg-white p-5" open={activeDetail === "dag"} onToggle={(event) => !event.currentTarget.open && setActiveDetail(null)}>
            <summary className="cursor-pointer text-sm font-medium text-stone-700">打开完整 DAG</summary>
            <div className="mt-5">

      {metrics.isLoading ? (
        <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
          正在加载已接受的运行指标…
        </p>
      ) : metrics.error || !metrics.data ? (
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
          <h2 className="font-semibold text-amber-100">运行指标不可用</h2>
          <p className="mt-2 text-sm text-amber-200/80">
            {metrics.error?.message ?? "无法获取完整且受边界约束的指标投影。"}
          </p>
          <p className="mt-2 text-xs text-amber-200/60">
            浏览器不会推断不完整的计数，也不会根据缺失指标判定运行成功。
          </p>
        </div>
      ) : (
        <RunMetrics metrics={metrics.data} />
      )}

      <GitHubPublication runId={runId} runStatus={run.data.status} />

      {dag.isLoading ? (
        <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
          正在加载已验证的 DAG…
        </p>
      ) : dag.error || !dag.data ? (
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
          <h2 className="font-semibold text-amber-100">任务 DAG 不可用</h2>
          <p className="mt-2 text-sm text-amber-200/80">
            {dag.error?.message ?? "当前运行无法获取已验证的拓扑结构。"}
          </p>
          <p className="mt-2 text-xs text-amber-200/60">
            浏览器不会根据任务顺序推断缺失的依赖边。
          </p>
        </div>
      ) : (
        <RunDAG runId={runId} dag={dag.data} />
      )}
            </div>
          </details>

      <details className="rounded-2xl border border-stone-200 bg-white p-5" open={activeDetail === "activity"}>
      <summary className="cursor-pointer text-sm font-medium text-stone-700">原始 SSE 事件记录</summary>
      <div className="mt-5 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-white">实时运行时间线</h2>
            <p className="mt-1 text-sm text-slate-500">
              SSE 用于观察已接受的运行时事件；运行成功仍由结构化持久化和确定性证据门控决定。
            </p>
          </div>
          <StreamBadge status={streamStatus} sequence={lastSequenceRef.current} />
        </div>

        {streamError ? (
          <p className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3 text-sm text-rose-200">
            {streamError}
          </p>
        ) : null}

        <div className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950/60">
          {events.length === 0 ? (
            <p className="p-5 text-sm text-slate-500">
              {streamStatus === "unsupported"
                ? "当前浏览器不支持 EventSource。"
                : "正在等待已接受的运行时事件…"}
            </p>
          ) : (
            <ol className="divide-y divide-slate-800">
              {events.map((event) => (
                <li key={event.event_id} className="grid gap-3 p-4 md:grid-cols-[5rem_11rem_1fr]">
                  <p className="font-mono text-xs text-slate-500">#{event.sequence}</p>
                  <div className="space-y-1">
                    <p className={`text-xs font-semibold ${levelClass(event.level)}`}>
                      {labelFor(event.level)}
                    </p>
                    <p className="text-xs text-slate-500">{labelFor(event.source)}</p>
                  </div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-mono text-xs text-cyan-200">{labelFor(event.kind)}</p>
                      {event.task_id ? (
                        <span className="text-xs text-slate-500">任务 {event.task_id}</span>
                      ) : null}
                      {event.generation ? (
                        <span className="text-xs text-slate-500">代次 {event.generation}</span>
                      ) : null}
                    </div>
                    <p className="mt-2 text-sm text-slate-300">
                      {translateRuntimeEventMessage(event.kind, event.message)}
                    </p>
                    <p className="mt-2 text-xs text-slate-600">
                      {formatDateTime(event.created_at)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
      </details>

      <details className="rounded-2xl border border-stone-200 bg-white p-5">
        <summary className="cursor-pointer text-sm font-medium text-stone-700">所有任务与证据</summary>
        <div className="mt-5 space-y-3">
        <h2 className="text-xl font-semibold text-white">任务</h2>
        {run.data.tasks.map((task) => (
          <Link
            key={task.task_id}
            to={`/runs/${run.data.run_id}/tasks/${encodeURIComponent(task.task_id)}`}
            className="block rounded-xl border border-slate-800 bg-slate-900/50 p-5 transition hover:border-slate-700"
          >
            <div className="flex flex-wrap justify-between gap-4">
              <div>
                <p className="font-mono text-sm text-cyan-200">{task.task_id}</p>
                <p className="mt-2 text-slate-300">
                  {translateTaskObjective(task.objective)}
                </p>
              </div>
              <p className="text-sm text-slate-500">
                {task.evidence_count} 条证据记录
              </p>
            </div>
          </Link>
        ))}
        </div>
      </details>
      <details className="rounded-2xl border border-stone-200 bg-white p-5">
        <summary className="cursor-pointer text-sm font-medium text-stone-700">运行控制与人工决策</summary>
        <div className="mt-5 space-y-6"><OperatorRecoveryPanel runId={runId} /><HumanGatePanel runId={runId} /></div>
      </details>
        </div>
        <ContextDrawer
          run={run.data}
          metrics={metrics.data ?? null}
          currentTask={currentTask}
          streamStatus={streamStatus}
        >
          <DependencyEnvironmentPanel projectId={run.data.project_id} />
          <GitHubPublication runId={runId} runStatus={run.data.status} />
        </ContextDrawer>
      </div>
    </section>
  );
}

function RunHero({
  run,
  currentTask,
  metrics,
  nodes,
  completedTaskCount,
  latestEvent,
  onOpenActivity,
  onOpenDag,
}: {
  run: ProductRunDetail;
  currentTask: ProductDAGNode | null;
  metrics: ProductRunMetrics | null;
  nodes: readonly ProductDAGNode[];
  completedTaskCount: number;
  latestEvent: RuntimeEventSummary | null;
  onOpenActivity: () => void;
  onOpenDag: () => void;
}) {
  const statusText = run.status === "RUNNING" ? "运行中" : labelFor(run.status);
  const unlockedInterfaces = nodes
    .filter((node) => node.presentation_state === "SUCCEEDED")
    .reduce((count, node) => count + (node.produces?.length ?? 0), 0);
  const developmentBudget = metrics?.token_budget.stages?.find(
    (item) => item.stage === "DEVELOPMENT",
  );
  return (
    <header className="rounded-2xl border border-stone-200 bg-white px-6 py-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0">
          <p className="text-sm text-stone-500">{run.repository_url.replace(/\/$/, "").split("/").slice(-2).join("/")} / {run.default_branch}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-stone-900">运行看板</h1>
          <p className="mt-2 font-mono text-xs text-stone-400">{run.run_id}</p>
        </div>
        <span className={`rounded-full px-3 py-1.5 text-sm font-medium ${run.status === "SUCCEEDED" ? "bg-emerald-50 text-emerald-700" : run.status === "FAILED" ? "bg-rose-50 text-rose-700" : "bg-amber-50 text-amber-700"}`}>{statusText}</span>
      </div>
      <div className="mt-5 grid gap-4 border-t border-stone-100 pt-5 md:grid-cols-[1fr_1.35fr_auto] md:items-center">
        <p className="text-sm text-stone-600"><span className="font-semibold text-stone-900">已完成 {completedTaskCount} / {run.task_count} 个工作包</span><br /><span className="text-stone-400">已解锁 {unlockedInterfaces} 个接口 · 当前：{currentTask?.task_id ?? (run.status === "SUCCEEDED" ? "已完成" : "等待调度")} · {executionModeLabel(currentTask?.execution_mode)}</span>{developmentBudget ? <><br /><span className="text-stone-400">开发预算 {developmentBudget.used_tokens.toLocaleString()} / {developmentBudget.total_budget_tokens.toLocaleString()}</span></> : null}</p>
        <p className="text-sm text-stone-500">{latestEvent ? `最近进展：${translateRuntimeEventMessage(latestEvent.kind, latestEvent.message)}` : "最近进展：等待运行事件"}</p>
        <div className="flex flex-wrap items-center justify-end gap-2"><span className="rounded-full bg-stone-100 px-2.5 py-1 text-xs text-stone-600">{metrics ? `策略：${metrics.workflow.activation_mode}` : "正在读取策略"}</span><button type="button" onClick={onOpenActivity} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 hover:bg-stone-50">实时过程</button><button type="button" onClick={onOpenDag} className="rounded-lg border border-stone-200 px-3 py-2 text-sm text-stone-600 hover:bg-stone-50">完整 DAG</button></div>
      </div>
    </header>
  );
}

function TaskRail({ runId, nodes }: { runId: string; nodes: readonly ProductDAGNode[] }) {
  if (!nodes.length) return <section className="rounded-2xl border border-stone-200 bg-white p-5 text-sm text-stone-500">正在读取任务计划…</section>;
  return (
    <section className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm" aria-label="任务进度">
      <div className="flex items-center justify-between"><h2 className="font-semibold text-stone-900">任务进度</h2><span className="text-xs text-stone-400">按依赖关系排序</span></div>
      <ol className="mt-4 grid gap-1">
        {nodes.map((node) => <li key={node.task_id}><Link to={`/runs/${runId}/tasks/${encodeURIComponent(node.task_id)}`} className="flex items-start gap-3 rounded-xl px-2 py-2 hover:bg-stone-50" aria-label={`查看任务 ${node.task_id}`}><span aria-hidden="true" className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${taskDotClass(node.presentation_state)}`} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-stone-700">{node.task_id}</span><span className="block truncate text-xs text-stone-400">{translateTaskObjective(node.objective)}</span><span className="mt-1 block truncate text-[11px] text-stone-400">工作包 · 文件 {node.owned_paths?.length ?? 0} · 接口 +{node.produces?.length ?? 0} / 依赖 {node.consumes?.length ?? 0}{node.package_budget_tokens != null ? ` · 预算 ${node.package_used_tokens ?? 0}/${node.package_budget_tokens}` : ""}</span><span className="block truncate text-[11px] text-stone-400">验证：{node.verification_commands?.[0] ?? "未声明"}</span></span><span className="shrink-0 rounded bg-stone-100 px-1.5 py-0.5 text-[11px] text-stone-500">{executionModeLabel(node.execution_mode)}</span><span className="shrink-0 text-xs text-stone-500">{taskStateLabel(node.presentation_state)}</span></Link></li>)}
      </ol>
    </section>
  );
}

function ContextDrawer({ run, metrics, currentTask, streamStatus, children }: { run: ProductRunDetail; metrics: ProductRunMetrics | null; currentTask: ProductDAGNode | null; streamStatus: StreamStatus; children: ReactNode }) {
  return (
    <aside className="hidden space-y-4 xl:block" aria-label="运行上下文">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm"><h2 className="text-sm font-semibold text-stone-900">当前任务</h2><p className="mt-2 font-mono text-sm text-blue-700">{currentTask?.task_id ?? (run.status === "SUCCEEDED" ? "已完成" : "等待调度")}</p><p className="mt-2 text-xs leading-5 text-stone-500">{currentTask ? translateTaskObjective(currentTask.objective) : run.status === "SUCCEEDED" ? "所有任务均已完成。" : "任务会在其依赖完成后开始。"}</p><dl className="mt-3 grid gap-2 border-t border-stone-100 pt-3 text-xs text-stone-500"><div><dt className="inline">执行方式：</dt><dd className="inline text-stone-700">{executionModeLabel(currentTask?.execution_mode)}</dd></div><div><dt className="inline">当前步骤：</dt><dd className="inline text-stone-700">{currentTask?.workflow_step ?? (currentTask?.execution_mode === "AGENT" ? "Agent 开发" : "等待执行")}</dd></div>{currentTask?.agent_escalation_reason ? <div><dt>升级原因：</dt><dd className="mt-1 leading-5 text-amber-700">{currentTask.agent_escalation_reason}</dd></div> : null}</dl><div className="mt-3 border-t border-stone-100 pt-3 text-xs text-stone-500">事件流：{labelFor(streamStatus)} · 基线 {run.base_commit.slice(0, 8)}</div></section>
      <section className="rounded-2xl border border-stone-200 bg-white p-4"><h2 className="text-sm font-semibold text-stone-900">运行数据</h2><dl className="mt-3 grid grid-cols-2 gap-3 text-sm"><div><dt className="text-stone-400">本次 Token</dt><dd className="mt-1 font-medium text-stone-700">{metrics ? `${metrics.token_budget.used_total_tokens.toLocaleString()} / ${metrics.token_budget.total_budget_tokens.toLocaleString()}` : "—"}</dd></div><div><dt className="text-stone-400">规划 Token</dt><dd className="mt-1 font-medium text-stone-700">{metrics?.planning_budget ? `${metrics.planning_budget.used_total_tokens.toLocaleString()} / ${metrics.planning_budget.total_budget_tokens.toLocaleString()}` : "—"}</dd></div><div><dt className="text-stone-400">预算状态</dt><dd className="mt-1 font-medium text-stone-700">{metrics?.token_budget.status ?? "—"}</dd></div><div><dt className="text-stone-400">上下文复用</dt><dd className="mt-1 font-medium text-stone-700">{metrics?.performance.context_reused_files ?? "—"} 文件</dd></div><div><dt className="text-stone-400">预留 Token</dt><dd className="mt-1 font-medium text-stone-700">{metrics?.token_budget.reserved_tokens.toLocaleString() ?? "—"}</dd></div></dl>{metrics?.token_budget.stages?.length ? <div className="mt-4 border-t border-stone-100 pt-3"><p className="text-xs font-medium text-stone-500">阶段预算</p><ul className="mt-2 space-y-1 text-xs text-stone-600">{metrics.token_budget.stages.map((item) => <li key={item.stage} className="flex justify-between gap-2"><span>{stageBudgetLabel(item.stage)}</span><span>{item.used_tokens.toLocaleString()} / {item.total_budget_tokens.toLocaleString()}</span></li>)}</ul></div> : null}{metrics?.token_budget.work_packages?.length ? <div className="mt-4 border-t border-stone-100 pt-3"><p className="text-xs font-medium text-stone-500">工作包预算</p><ul className="mt-2 space-y-2 text-xs text-stone-600">{metrics.token_budget.work_packages.map((item) => <li key={item.task_id}><div className="flex justify-between gap-2"><span className="truncate">{item.task_id} · {item.complexity}</span><span>{item.developer_used_tokens + item.repair_used_tokens} / {item.total_budget_tokens}</span></div><p className="mt-1 text-[11px] text-stone-400">开发 {item.developer_used_tokens}/{item.developer_budget_tokens} · 修复 {item.repair_used_tokens}/{item.repair_budget_tokens} · 借款 {(item.developer_borrowed_tokens ?? 0) + (item.repair_borrowed_tokens ?? 0)} · 回收 {(item.developer_reclaimed_tokens ?? 0) + (item.repair_reclaimed_tokens ?? 0)}</p>{item.last_required_tokens ? <p className="mt-1 text-[11px] text-stone-400">下一轮需 {item.last_required_tokens} · 包可用 {item.last_available_tokens ?? 0} · FLEX {item.last_flex_available_tokens ?? 0}{item.last_budget_decision ? ` · ${item.last_budget_decision}` : ""}</p> : null}</li>)}</ul></div> : null}</section>
      {children}
    </aside>
  );
}

function stageBudgetLabel(stage: string): string {
  return ({ PLANNING: "规划", DEVELOPMENT: "开发", VERIFICATION_REPAIR: "验证与修复", REVIEW_PUBLICATION: "审查与发布", FLEX: "FLEX 弹性池" } as Record<string, string>)[stage] ?? stage;
}

function ActivityFeed({ events, streamStatus }: { events: readonly RuntimeEventSummary[]; streamStatus: StreamStatus }) {
  const latest = events.slice(-8).reverse();
  return <section aria-label="开发过程"><div className="flex items-center justify-between"><h2 className="font-semibold text-stone-900">开发过程</h2><span className="text-xs text-stone-400">事件流状态：{labelFor(streamStatus)}</span></div>{latest.length ? <ol className="mt-4 grid gap-2">{latest.map((event) => <li key={event.event_id} className="rounded-xl bg-stone-50 px-3 py-2.5"><p className="text-xs font-medium text-stone-500">#{event.sequence} · {activityStage(event.kind)} · {formatDateTime(event.created_at)}</p><p className="mt-1 text-sm text-stone-700">进展：{translateRuntimeEventMessage(event.kind, event.message)}</p></li>)}</ol> : <p className="mt-3 text-sm text-stone-500">正在等待已接受的运行时事件…</p>}</section>;
}

function activityStage(kind: string): string {
  if (kind.includes("VERIFICATION")) return "验证";
  if (kind.includes("REPAIR")) return "修复";
  if (kind.includes("REVIEW")) return "审查";
  if (kind.includes("DEVELOPER")) return "开发";
  return "准备";
}

function taskDotClass(state: ProductDAGNode["presentation_state"]): string {
  if (state === "SUCCEEDED") return "bg-emerald-500";
  if (state === "FAILED" || state === "BLOCKED") return "bg-rose-500";
  if (state === "BLOCKED_BY_CONTRACT") return "bg-amber-500";
  if (["RUNNING", "VERIFYING", "REPAIRING", "REVIEWING"].includes(state)) return "bg-blue-500";
  return "bg-stone-300";
}

function taskStateLabel(state: ProductDAGNode["presentation_state"]): string {
  return { PENDING: "等待", READY: "就绪", RUNNING: "开发中", VERIFYING: "验证中", REVIEWING: "审查中", REPAIRING: "修复中", SUCCEEDED: "已完成", FAILED: "失败", BLOCKED: "等待依赖", BLOCKED_BY_CONTRACT: "接口契约未满足" }[state];
}

function executionModeLabel(mode: ProductDAGNode["execution_mode"] | undefined): string {
  return { WORKFLOW: "Workflow", AGENT: "Agent", HYBRID: "Hybrid" }[mode ?? "AGENT"];
}

function FailureSummary({
  failures,
  checkpoint,
  explanation,
  explanationError,
  explaining,
  onExplain,
  retryError,
  retrying,
  onRetry,
  cacheReady,
  cachedRetrying,
  cachedRetryError,
  onCachedRetry,
  resumeError,
  resuming,
  onResume,
}: {
  failures: readonly ProductRunFailure[];
  checkpoint: ProductRunCheckpoint | null;
  explanation: ProductFailureExplanation | undefined;
  explanationError: string | null;
  explaining: boolean;
  onExplain: () => void;
  retryError: string | null;
  retrying: boolean;
  onRetry: () => void;
  cacheReady: boolean;
  cachedRetrying: boolean;
  cachedRetryError: string | null;
  onCachedRetry: () => void;
  resumeError: string | null;
  resuming: boolean;
  onResume: () => void;
}) {
  const orderedFailures = [...failures].sort(
    (left, right) => failurePriority(left) - failurePriority(right),
  );
  return (
    <section
      aria-label="失败原因"
      className="rounded-xl border border-rose-400/30 bg-rose-400/5 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-rose-100">失败原因</h2>
          <p className="mt-1 text-sm text-rose-100/80">
            原运行已结束。重新发起会使用服务端保存的任务图创建新的运行记录，不会修改旧记录。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onExplain}
            disabled={explaining}
            className="rounded-lg border border-cyan-300/40 bg-cyan-300/10 px-4 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-300/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {explaining ? "正在生成解读…" : "AI 解读失败原因"}
          </button>
          {checkpoint ? (
            <button
              type="button"
              onClick={onResume}
              disabled={resuming}
              className="rounded-lg border border-emerald-300/40 bg-emerald-300/10 px-4 py-2 text-sm font-medium text-emerald-100 transition hover:bg-emerald-300/20 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {resuming ? "正在继续…" : "从检查点继续"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="rounded-lg border border-rose-300/40 bg-rose-300/10 px-4 py-2 text-sm font-medium text-rose-100 transition hover:bg-rose-300/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {retrying ? "正在重新发起…" : "重新发起运行"}
          </button>
          <button
            type="button"
            onClick={onCachedRetry}
            disabled={!cacheReady || cachedRetrying}
            className="rounded-lg border border-emerald-300/40 bg-emerald-300/10 px-4 py-2 text-sm font-medium text-emerald-100 transition hover:bg-emerald-300/20 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cachedRetrying ? "正在使用缓存环境…" : "使用缓存环境重新发起"}
          </button>
        </div>
      </div>

      {explanation ? (
        <div className="mt-4 rounded-lg border border-cyan-300/20 bg-cyan-300/5 p-4">
          <h3 className="font-medium text-cyan-100">AI 辅助说明</h3>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-cyan-50/90">
            {plainAiText(explanation.explanation)}
          </p>
          <p className="mt-3 text-xs text-cyan-100/60">
            {explanation.cached ? "已使用已保存的解读" : "刚刚生成"} · 模型 {explanation.model} ·
            仅用于辅助理解，不改变系统失败判定或重试权限。
          </p>
        </div>
      ) : null}

      {explanationError ? (
        <p className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/5 p-3 text-sm text-amber-100">
          AI 解读暂不可用：{explanationError}。原始失败证据仍可用于诊断。
        </p>
      ) : null}

      {retryError ? (
        <p className="mt-4 rounded-lg border border-rose-300/20 bg-slate-950/30 p-3 text-sm text-rose-100">
          重新发起失败：{retryError}
        </p>
      ) : null}

      {cachedRetryError ? (
        <p className="mt-4 rounded-lg border border-rose-300/20 bg-slate-950/30 p-3 text-sm text-rose-100">
          缓存环境重试失败：{cachedRetryError}
        </p>
      ) : null}

      {checkpoint ? (
        <div className="mt-4 rounded-lg border border-emerald-300/20 bg-emerald-300/5 p-4 text-sm text-emerald-50/90">
          <p className="font-medium text-emerald-100">可恢复检查点：任务 {checkpoint.task_id}</p>
          <p className="mt-1">{checkpoint.summary}</p>
          <p className="mt-2 font-mono text-xs text-emerald-100/70">
            提交 {checkpoint.commit_sha.slice(0, 12)} · 已保存 {checkpoint.changed_files.length} 个文件 · 原因 {checkpoint.reason}
          </p>
        </div>
      ) : null}

      {resumeError ? (
        <p className="mt-4 rounded-lg border border-rose-300/20 bg-slate-950/30 p-3 text-sm text-rose-100">
          从检查点继续失败：{resumeError}
        </p>
      ) : null}

      {failures.length === 0 ? (
        <p className="mt-4 text-sm text-rose-100/80">
          后端未找到可展示的结构化失败报告；请查看下方运行时间线并保留该运行 ID 以便诊断。
        </p>
      ) : (
        <ol className="mt-4 space-y-3">
          {orderedFailures.map((failure, index) => (
            <li key={`${failure.task_id ?? "run"}-${failure.failure_type}-${index}`} className="rounded-lg border border-rose-300/15 bg-slate-950/30 p-4">
              <p className="font-medium text-rose-100">
                {failure.task_id ? `任务 ${failure.task_id}：` : "运行："}
                {failure.message}
              </p>
              <p className="mt-2 text-xs text-rose-100/70">
                类型：{failureTypeLabel(failure)} · 来源：{failure.source} · {failure.retryable ? "允许自动重试" : "不可自动重试"}
              </p>
              {failure.evidence.length > 0 ? (
                <pre className="mt-3 overflow-x-auto rounded bg-slate-950/70 p-3 text-xs leading-5 text-rose-100/90">
                  {failure.evidence.join("\n")}
                </pre>
              ) : null}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function plainAiText(value: string): string {
  return value
    .replace(/^#{1,6}\s*/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
}

function failureTypeLabel(failure: ProductRunFailure): string {
  // Existing persisted runs predate AGENT_TIME_LIMIT. Preserve their diagnostic meaning instead
  // of showing a historical TIME_LIMIT result as an unrelated tool fault.
  if (
    failure.failure_type === "TOOL_FAILURE" &&
    failure.source === "runtime" &&
    failure.evidence.includes("stop_reason=TIME_LIMIT")
  ) {
    return "开发智能体时间预算耗尽";
  }

  return {
    MODEL_TIMEOUT: "模型响应超时",
    AGENT_TIME_LIMIT: "开发智能体时间预算耗尽",
    RATE_LIMIT: "模型服务限流",
    INVALID_AGENT_OUTPUT: "智能体输出无效",
    TOOL_FAILURE: "运行环境或工具故障",
    SCOPE_VIOLATION: "超出文件修改范围",
    TEST_FAILURE: "验证命令未通过",
    LINT_FAILURE: "代码检查未通过",
    REVIEW_REJECTED: "代码审查未通过",
    CONTEXT_OVERFLOW: "上下文超出限制",
    MERGE_CONFLICT: "代码合并冲突",
    SANDBOX_TIMEOUT: "验证沙箱超时",
    VERIFICATION_ENV_UNAVAILABLE: "验证环境不可用",
    TOKEN_BUDGET_EXHAUSTED: "本次运行模型预算已用尽",
    INTERFACE_CONTRACT_UNMET: "接口契约未满足",
  }[failure.failure_type];
}

function failurePriority(failure: ProductRunFailure): number {
  return ({
    INTERFACE_CONTRACT_UNMET: 1,
    TEST_FAILURE: 2,
    LINT_FAILURE: 2,
    TOKEN_BUDGET_EXHAUSTED: 3,
    PROVIDER_FAILURE: 5,
    TOOL_FAILURE: 5,
  } as Record<string, number>)[failure.failure_type] ?? 4;
}

function LaunchNotice({
  launch,
}: {
  launch: RunLaunchResponse | RequirementRunLaunchResponse;
}) {
  if ("launch_state" in launch) {
    const queued = launch.dispatches.filter((item) => item.state === "QUEUED").length;
    return (
      <div
        className={[
          "rounded-xl border p-4 text-sm",
          launch.launch_state === "QUEUED"
            ? "border-emerald-400/20 bg-emerald-400/5 text-emerald-200"
            : "border-amber-400/20 bg-amber-400/5 text-amber-100",
        ].join(" ")}
      >
        多智能体启动：{labelFor(launch.launch_state)} · 已排队 {queued}/{launch.dispatches.length} 个根任务
      </div>
    );
  }

  return (
    <div
      className={[
        "rounded-xl border p-4 text-sm",
        launch.dispatch_status === "QUEUED"
          ? "border-emerald-400/20 bg-emerald-400/5 text-emerald-200"
          : "border-amber-400/20 bg-amber-400/5 text-amber-100",
      ].join(" ")}
    >
      分派状态：{labelFor(launch.dispatch_status)}
      {launch.detail ? ` · ${launch.detail}` : ""}
    </div>
  );
}

type MetricProps = {
  label: string;
  value: string;
  mono?: boolean;
};

function Metric({ label, value, mono = false }: MetricProps) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-2 text-slate-200 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function StreamBadge({
  status,
  sequence,
}: {
  status: StreamStatus;
  sequence: number;
}) {
  const label = {
    connecting: "正在连接",
    live: "实时连接",
    reconnecting: "正在重连",
    unsupported: "浏览器不支持",
    error: "事件流错误",
  }[status];

  return (
    <span className="rounded-full border border-slate-700 bg-slate-900 px-3 py-1 font-mono text-xs text-slate-300">
      {label}
      {sequence > 0 ? ` · #${sequence}` : ""}
    </span>
  );
}

function levelClass(level: RuntimeEventSummary["level"]): string {
  if (level === "ERROR") {
    return "text-rose-300";
  }
  if (level === "WARNING") {
    return "text-amber-300";
  }
  return "text-emerald-300";
}
