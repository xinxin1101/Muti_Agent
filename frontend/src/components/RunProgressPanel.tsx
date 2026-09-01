import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  getOperatorRecovery,
  getRunRecoveryPreview,
  getRuntimeDependencyHealth,
  recoverInterruptedRun,
} from "../api/product";
import { formatDateTime, labelFor } from "../i18n";
import type { RequirementRunLaunchResponse } from "../api/product";
import type { ProductRun } from "../types/product";
import type { RuntimeEventSummary } from "../types/runtime";

type DisplayState =
  | "RUNNING"
  | "WAITING_EXTERNAL"
  | "RECOVERY_REQUIRED"
  | "FAILED"
  | "SUCCEEDED";

type Props = Readonly<{
  run: ProductRun;
  events: readonly RuntimeEventSummary[];
  onRecovered: (nextRunId: string, launch: RequirementRunLaunchResponse) => void;
}>;

export function RunProgressPanel({ run, events, onRecovered }: Props) {
  const now = useRefreshClock(run.status === "RUNNING");
  const recovery = useQuery({
    queryKey: ["operator-recovery", run.run_id],
    queryFn: () => getOperatorRecovery(run.run_id),
    enabled: run.status === "RUNNING",
  });
  const recoveryPreview = useQuery({
    queryKey: ["run-recovery-preview", run.run_id],
    queryFn: () => getRunRecoveryPreview(run.run_id),
    enabled: run.status === "RUNNING" && run.display_status === "RECOVERY_REQUIRED",
    refetchInterval: run.status === "RUNNING" && run.display_status === "RECOVERY_REQUIRED" ? 15_000 : false,
  });
  const dependencies = useQuery({
    queryKey: ["runtime-dependency-health"],
    queryFn: getRuntimeDependencyHealth,
    refetchInterval: run.status === "RUNNING" ? 15_000 : false,
  });
  const recover = useMutation({
    mutationFn: () => recoverInterruptedRun(run.run_id),
    onSuccess: (launch) => onRecovered(launch.run_id, launch),
  });

  const experience = describeExperience(run, recovery.data, events, now);
  const lastProgress = effectiveProgressEvent(events);
  const activity = latestActivity(events);
  const continuation = latestContinuation(events);
  const elapsed = elapsedLabel(run.started_at, run.finished_at, now);

  return (
    <section aria-label="运行进展" className="df-surface-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-stone-900">运行进展</h2>
          <p className="mt-1 text-sm text-stone-600">{experience.detail}</p>
        </div>
        <span className={`rounded-full border px-3 py-1 text-xs font-semibold ${badgeClass(experience.state)}`}>
          {labelFor(experience.state)}
        </span>
      </div>

      <dl className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Metric label="总体完成" value={`${experience.completed}/${run.task_count} 个任务`} />
        <Metric label="当前阶段" value={experience.phase} />
        <Metric
          label="开发切片"
          value={continuation ? `第 ${continuation.sliceIndex}/${continuation.maxSlices} 个` : "第 1/1 个"}
        />
        <Metric label="已运行" value={elapsed} />
        <Metric
          label="最近有效进展"
          value={lastProgress ? formatDateTime(lastProgress.created_at) : "尚无有效进展证据"}
        />
      </dl>

      <div className="mt-4 rounded-lg border border-stone-200 bg-stone-50 p-4 text-sm text-stone-800">
        <p>
          <span className="text-stone-500">当前任务：</span>
          {experience.taskId ?? "等待调度"}
        </p>
        <p className="mt-2 text-xs leading-5 text-stone-600">
          租约心跳仅说明 Worker 仍持有执行权；“最近有效进展”只计算运行开始、分派、证据、验证、审查、修复和终态事件，不将租约续期计入进度。
        </p>
      </div>

      <p className={`mt-3 text-xs ${dependencies.data?.dispatch_available ? "text-emerald-700" : "text-amber-700"}`}>
        {dependencies.data
          ? dependencies.data.dispatch_available
            ? "运行依赖正常：PostgreSQL 与 Redis 消息队列可用。"
            : `运行依赖异常：数据库 ${dependencies.data.database.state}，Redis ${dependencies.data.redis.state}。新的任务不会被伪装为正在执行。`
          : dependencies.isError
            ? "运行依赖状态暂不可读取；请检查 API、PostgreSQL 与 Redis。"
            : "正在检查 PostgreSQL 与 Redis 消息队列状态…"}
      </p>

      <ol aria-label="任务阶段" className="mt-4 flex flex-wrap gap-2 text-xs text-stone-600">
        {STAGES.map((stage) => (
          <li
            key={stage}
            className={[
              "rounded-full border px-2.5 py-1",
              stage === experience.phase ? "border-blue-200 bg-blue-50 text-blue-800" : "border-stone-200 bg-stone-50",
            ].join(" ")}
          >
            {stage}
          </li>
        ))}
      </ol>

      {activity ? (
        <div className="mt-4 rounded-lg border border-stone-200 bg-stone-50 p-4">
          <p className="text-sm font-medium text-stone-900">最近已持久化的开发活动</p>
          <div className="mt-3 grid gap-3 text-sm sm:grid-cols-3">
            <p><span className="text-stone-500">模型轮次：</span>{activity.iterations}</p>
            <p><span className="text-stone-500">工具调用：</span>{activity.toolCalls}</p>
            <p><span className="text-stone-500">模型响应：</span>{activity.latencyMs} ms</p>
          </div>
          <p className="mt-3 text-sm text-stone-800">
            <span className="text-stone-500">最近修改文件：</span>
            {activity.changedFiles.length > 0 ? activity.changedFiles.join("、") : "本轮未修改文件"}
            {activity.changedFileCount > activity.changedFiles.length
              ? `（另有 ${activity.changedFileCount - activity.changedFiles.length} 个）`
              : ""}
          </p>
        </div>
      ) : null}

      {continuation ? (
        <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">
          已从检查点自动续接：累计 {formatElapsedMs(continuation.elapsedMs)}。
          {continuation.remainingSummary ? ` 下一切片：${continuation.remainingSummary}` : ""}
        </div>
      ) : null}

      {experience.state === "RECOVERY_REQUIRED" ? (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="font-medium text-amber-900">该运行已异常中断，不能安全地在原记录中重试。</p>
          <p className="mt-1 text-sm text-amber-800">
            {recoveryPreview.data?.reason ?? run.recovery_reason ?? "Worker 已释放租约但没有保存终态执行证据。"}
            新建恢复运行会使用服务器保存的任务图和最新仓库基线，不修改这条旧记录。
          </p>
          {recoveryPreview.data ? (
            <p className="mt-2 text-xs text-amber-700">
              将复用 {recoveryPreview.data.reusable_task_ids.length} 个已完成工作包，剩余 {recoveryPreview.data.remaining_task_ids.length} 个；预计新增 {recoveryPreview.data.estimated_new_budget_tokens.toLocaleString()} Token。
              {recoveryPreview.data.baseline_changed ? " 当前仓库基线已变化，请确认基于旧基线继续。" : " 当前基线未变化，可安全复用。"}
            </p>
          ) : null}
          <button
            type="button"
            onClick={() => recover.mutate()}
            disabled={recover.isPending}
            className="df-button mt-3 border border-amber-300 bg-amber-50 text-amber-900"
          >
            {recover.isPending ? "正在创建恢复运行…" : "新建恢复运行"}
          </button>
          {recover.error instanceof Error ? (
            <p className="mt-3 text-sm text-rose-700">创建恢复运行失败：{recover.error.message}</p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function Metric({ label, value }: Readonly<{ label: string; value: string }>) {
  return (
    <div className="rounded-lg border border-stone-200 bg-stone-50 p-3">
      <dt className="text-xs text-stone-500">{label}</dt>
      <dd className="mt-1 text-sm font-medium text-stone-900">{value}</dd>
    </div>
  );
}

function describeExperience(
  run: ProductRun,
  recovery: Awaited<ReturnType<typeof getOperatorRecovery>> | undefined,
  events: readonly RuntimeEventSummary[],
  now: number,
): Readonly<{ state: DisplayState; completed: number; taskId: string | null; phase: string; detail: string }> {
  if (run.display_status === "RECOVERY_REQUIRED") {
    return {
      state: "RECOVERY_REQUIRED",
      completed: recovery?.reconciliation.completed_task_ids.length ?? 0,
      taskId: recovery?.reconciliation.reconcile_task_ids[0] ?? null,
      phase: "异常中断",
      detail: run.recovery_reason ?? "检测到运行缺少有效 Worker 终态证据。",
    };
  }
  if (run.display_status === "WAITING_EXTERNAL") {
    return {
      state: "WAITING_EXTERNAL",
      completed: recovery?.reconciliation.completed_task_ids.length ?? 0,
      taskId: null,
      phase: "等待外部响应",
      detail: run.recovery_reason ?? "正在等待模型、消息队列或外部服务响应。",
    };
  }
  if (run.status === "SUCCEEDED" || run.status === "FAILED") {
    return {
      state: run.status,
      completed: run.status === "SUCCEEDED" ? run.task_count : recovery?.reconciliation.completed_task_ids.length ?? 0,
      taskId: null,
      phase: run.status === "SUCCEEDED" ? "已完成" : "已结束",
      detail: run.status === "SUCCEEDED" ? "所有接受的任务证据已完成。" : "该运行已结束，请查看失败原因或重新发起。",
    };
  }
  const tasks = recovery?.reconciliation.tasks ?? [];
  const gap = tasks.find((item) => item.frontier_state === "BLOCKED_RECOVERY_GAP");
  if (gap) {
    return {
      state: "RECOVERY_REQUIRED",
      completed: recovery?.reconciliation.completed_task_ids.length ?? 0,
      taskId: gap.task_id,
      phase: "异常中断",
      detail: "检测到租约已释放但缺少终态执行证据，已停止将其展示为正常运行。",
    };
  }
  const active = tasks.find((item) => item.frontier_state === "WAIT_ACTIVE_OWNER");
  if (active) {
    const last = effectiveProgressEvent(events);
    const stale = last !== null && now - Date.parse(last.created_at) > 90_000;
    return {
      state: stale ? "WAITING_EXTERNAL" : "RUNNING",
      completed: recovery?.reconciliation.completed_task_ids.length ?? 0,
      taskId: active.task_id,
      phase: stageFromEvents(events),
      detail: stale
        ? "当前 Worker 仍持有租约，但近期没有新的模型、工具或验证证据。"
        : "任务执行中，正在等待模型、工具或验证阶段产生可验证进展。",
    };
  }
  const next = tasks.find((item) => item.frontier_state === "RECONCILE_CANDIDATE")
    ?? tasks.find((item) => item.frontier_state === "WAIT_DEPENDENCIES");
  return {
    state: "RUNNING",
    completed: recovery?.reconciliation.completed_task_ids.length ?? 0,
    taskId: next?.task_id ?? null,
    phase: next?.frontier_state === "WAIT_DEPENDENCIES" ? "等待依赖" : "等待调度",
    detail: "正在根据持久化任务依赖和执行证据确定下一步。",
  };
}

function effectiveProgressEvent(events: readonly RuntimeEventSummary[]): RuntimeEventSummary | null {
  return [...events].reverse().find((event) => event.kind !== "LEASE_HEARTBEAT") ?? null;
}

function stageFromEvents(events: readonly RuntimeEventSummary[]): string {
  const evidence = [...events].reverse().find((event) =>
    event.kind === "EVIDENCE_RECORDED"
    && typeof event.attributes.evidence_kind === "string",
  );
  const kind = typeof evidence?.attributes.evidence_kind === "string"
    ? evidence.attributes.evidence_kind
    : null;
  return {
    DEVELOPER_RUN: "开发",
    VERIFICATION_RESULT: "验证",
    REPAIR_RUN: "修复",
    REVIEW_DECISION: "审查",
    WORKER_EXECUTION: "提交",
  }[kind ?? ""] ?? "准备验证环境";
}

type Activity = Readonly<{
  iterations: number;
  toolCalls: number;
  latencyMs: number;
  changedFiles: readonly string[];
  changedFileCount: number;
}>;

function latestActivity(events: readonly RuntimeEventSummary[]): Activity | null {
  for (const event of [...events].reverse()) {
    if (event.kind !== "EVIDENCE_RECORDED") continue;
    const raw = event.attributes.activity;
    if (!isRecord(raw)) continue;
    const changedFiles = Array.isArray(raw.changed_files)
      ? raw.changed_files.filter((item): item is string => typeof item === "string")
      : [];
    if (
      typeof raw.iterations !== "number"
      || typeof raw.tool_calls !== "number"
      || typeof raw.latency_ms !== "number"
      || typeof raw.changed_file_count !== "number"
    ) continue;
    return {
      iterations: raw.iterations,
      toolCalls: raw.tool_calls,
      latencyMs: raw.latency_ms,
      changedFiles,
      changedFileCount: raw.changed_file_count,
    };
  }
  return null;
}

type Continuation = Readonly<{
  sliceIndex: number;
  maxSlices: number;
  elapsedMs: number;
  remainingSummary: string;
}>;

function latestContinuation(events: readonly RuntimeEventSummary[]): Continuation | null {
  for (const event of [...events].reverse()) {
    if (event.kind !== "EVIDENCE_RECORDED") continue;
    const raw = event.attributes.continuation;
    if (!isRecord(raw)) continue;
    if (
      typeof raw.slice_index !== "number"
      || typeof raw.max_slices !== "number"
      || typeof raw.elapsed_ms !== "number"
    ) continue;
    return {
      sliceIndex: raw.slice_index,
      maxSlices: raw.max_slices,
      elapsedMs: raw.elapsed_ms,
      remainingSummary: typeof raw.remaining_summary === "string" ? raw.remaining_summary : "",
    };
  }
  return null;
}

function formatElapsedMs(value: number): string {
  return value < 1_000 ? `${value} ms` : `${(value / 1_000).toFixed(1)} 秒`;
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function elapsedLabel(startedAt: string, finishedAt: string | null, now: number): string {
  const milliseconds = Math.max(0, (finishedAt ? Date.parse(finishedAt) : now) - Date.parse(startedAt));
  const totalSeconds = Math.floor(milliseconds / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes > 0 ? `${minutes} 分 ${seconds} 秒` : `${seconds} 秒`;
}

const STAGES = ["准备验证环境", "准备工作区", "开发", "验证", "修复", "审查", "提交"] as const;

function useRefreshClock(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    const intervalId = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(intervalId);
  }, [enabled]);
  return now;
}

function badgeClass(state: DisplayState): string {
  if (state === "SUCCEEDED") return "border-emerald-200 bg-emerald-50 text-emerald-800";
  if (state === "FAILED" || state === "RECOVERY_REQUIRED") return "border-rose-200 bg-rose-50 text-rose-800";
  if (state === "WAITING_EXTERNAL") return "border-amber-200 bg-amber-50 text-amber-800";
  return "border-blue-200 bg-blue-50 text-blue-800";
}
