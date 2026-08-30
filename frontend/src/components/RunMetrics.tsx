import type { ProductRunMetrics } from "../types/product";
import { labelFor } from "../i18n";

export function RunMetrics({ metrics }: { metrics: ProductRunMetrics }) {
  const roleUsage = new Map(metrics.token_budget.roles.map((role) => [role.role, role]));
  return (
    <section className="space-y-4" aria-label="运行指标">
      <div>
        <h2 className="text-xl font-semibold text-white">运行指标</h2>
        <p className="mt-1 text-sm text-slate-500">
          来自已接受持久化记录和运行时事件的描述性计数。状态仍来自 {labelFor(metrics.status_basis)}；这些数字不用于判定成功。
        </p>
      </div>

      <dl className="grid gap-3 md:grid-cols-4">
        <MetricCard
          label="终态耗时"
          value={formatDuration(metrics.terminal_duration_ms)}
        />
        <MetricCard label="证据记录" value={String(metrics.evidence.total_records)} />
        <MetricCard label="运行时事件" value={String(metrics.runtime_events.total_events)} />
        <MetricCard
          label="最新事件序列"
          value={`#${metrics.runtime_events.latest_sequence}`}
          mono
        />
      </dl>

      <div className="rounded-xl border border-blue-400/20 bg-blue-400/5 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold text-blue-100">执行路径</h3>
          <span className="font-mono text-sm text-blue-200">
            策略：{metrics.workflow.activation_mode}
          </span>
        </div>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="Workflow 任务" value={metrics.workflow.workflow_tasks} />
          <Metric label="Agent 任务" value={metrics.workflow.agent_tasks} />
          <Metric label="Hybrid 任务" value={metrics.workflow.hybrid_tasks} />
          <Metric label="Agent 调用" value={metrics.workflow.agent_calls} />
          <Metric label="Workflow 调用" value={metrics.workflow.workflow_calls} />
          <Metric label="Workflow 耗时" value={formatDuration(metrics.workflow.workflow_duration_ms)} />
          <Metric label="预计节省" value={`${metrics.workflow.estimated_tokens_saved.toLocaleString()} Token`} />
          <Metric label="升级至 Agent" value={metrics.workflow.agent_escalations} />
        </dl>
        <p className="mt-4 text-xs text-blue-100/70">
          “预计节省”基于被 Workflow 跳过的 Developer 输出上限估算，不等同于供应商账单；实际 Token 以预算统计为准。
        </p>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold text-slate-200">模型预算</h3>
          <span className="font-mono text-sm text-slate-300">
            {metrics.token_budget.used_total_tokens.toLocaleString()} / {metrics.token_budget.total_budget_tokens.toLocaleString()} Token · {metrics.token_budget.status}
          </span>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          预算在每次请求前预留，响应后按实际用量结算；超额请求会在本地拦截。
        </p>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {(["planner", "developer", "repair", "reviewer"] as const).map((role) => {
            const usage = roleUsage.get(role);
            return (
            <Metric
              key={role}
              label={`${roleLabel(role)}（${usage?.call_count ?? 0} 次）`}
              value={`${(usage?.total_tokens ?? 0).toLocaleString()} Token`}
            />
            );
          })}
          <Metric label="Workflow 调用（零模型 Token）" value={metrics.workflow.workflow_calls} />
          <Metric label="上下文估算" value={`${metrics.performance.context_estimated_tokens.toLocaleString()} Token`} />
          <Metric label="请求估算 / 实际" value={`${(metrics.performance.estimated_prompt_tokens ?? 0).toLocaleString()} / ${(metrics.performance.actual_prompt_tokens ?? 0).toLocaleString()}`} />
          <Metric label="复用文件" value={metrics.performance.context_reused_files} />
          <Metric label="裁剪文件" value={metrics.performance.context_trimmed_files} />
          <Metric label="已压缩工具组" value={metrics.performance.context_compacted_tool_groups ?? 0} />
        </dl>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h3 className="font-semibold text-slate-200">已接受的执行证据</h3>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Metric label="开发智能体执行" value={metrics.evidence.developer_runs} />
            <Metric label="验证尝试" value={metrics.evidence.verification_attempts} />
            <Metric label="审查结论" value={metrics.evidence.review_decisions} />
            <Metric label="修复尝试" value={metrics.evidence.repair_attempts} />
            <Metric label="失败报告" value={metrics.evidence.failure_reports} />
            <Metric label="工作进程执行" value={metrics.evidence.worker_executions} />
            <Metric label="分派事件" value={metrics.evidence.dispatch_events} />
            <Metric label="合并冲突" value={metrics.evidence.merge_conflicts} />
            <Metric label="人工决策" value={metrics.evidence.human_decisions} />
          </dl>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h3 className="font-semibold text-slate-200">运行时可观测性</h3>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Metric label="警告事件" value={metrics.runtime_events.warning_events} />
            <Metric label="错误事件" value={metrics.runtime_events.error_events} />
            <Metric label="获取租约" value={metrics.runtime_events.lease_acquisitions} />
            <Metric label="接管租约" value={metrics.runtime_events.lease_takeovers} />
            <Metric label="释放租约" value={metrics.runtime_events.lease_releases} />
            <Metric
              label="集成门控"
              value={metrics.evidence.integration_gate_evaluations}
            />
            <Metric
              label="合并快照"
              value={metrics.evidence.merge_queue_snapshots}
            />
          </dl>
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <h3 className="font-semibold text-slate-200">阶段耗时（已持久化）</h3>
        <p className="mt-1 text-sm text-slate-500">
          来自模型、工具、验证与 Worker 执行证据，用于识别耗时瓶颈，不参与成功判定。
        </p>
        <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Metric label="开发模型" value={formatDuration(metrics.performance.developer_model_latency_ms)} />
          <Metric label="修复模型" value={formatDuration(metrics.performance.repair_model_latency_ms)} />
          <Metric label="仓库工具" value={formatDuration(metrics.performance.repository_tool_latency_ms)} />
          <Metric label="确定性验证" value={formatDuration(metrics.performance.verification_latency_ms)} />
        </dl>
      </div>
    </section>
  );
}

function roleLabel(role: "planner" | "developer" | "repair" | "reviewer"): string {
  return {
    planner: "规划",
    developer: "开发",
    repair: "修复",
    reviewer: "审查",
  }[role];
}

function MetricCard({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-2 text-xl text-slate-100 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-mono text-lg text-slate-200">{value}</dd>
    </div>
  );
}

function formatDuration(value: number | null): string {
  if (value === null) {
    return "等待终态时间";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}
