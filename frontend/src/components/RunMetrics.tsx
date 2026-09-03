import type { ProductRunMetrics } from "../types/product";
import { labelFor } from "../i18n";

export function RunMetrics({ metrics }: { metrics: ProductRunMetrics }) {
  const roleUsage = new Map(metrics.token_budget.roles.map((role) => [role.role, role]));
  return (
    <section className="space-y-4" aria-label="运行指标">
      <div>
        <h2 className="text-xl font-semibold text-stone-900">运行指标</h2>
        <p className="mt-1 text-sm text-stone-600">
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

      <div className="rounded-xl border border-blue-200 bg-blue-50 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold text-blue-900">执行路径</h3>
          <span className="font-mono text-sm text-blue-800">
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
        <p className="mt-4 text-xs text-blue-700">
          “预计节省”基于被 Workflow 跳过的 Developer 输出上限估算，不等同于供应商账单；实际 Token 以预算统计为准。
        </p>
      </div>

      <div className="rounded-xl border border-stone-200 bg-white p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-semibold text-stone-900">模型预算</h3>
          <span className="font-mono text-sm text-stone-700">
            {metrics.token_budget.used_total_tokens.toLocaleString()} / {metrics.token_budget.total_budget_tokens.toLocaleString()} Token · {metrics.token_budget.status}
          </span>
        </div>
        <p className="mt-1 text-sm text-stone-600">
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

      <TokenBudgetAudit metrics={metrics} />

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-stone-200 bg-white p-5">
          <h3 className="font-semibold text-stone-900">已接受的执行证据</h3>
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

        <div className="rounded-xl border border-stone-200 bg-white p-5">
          <h3 className="font-semibold text-stone-900">运行时可观测性</h3>
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

      <div className="rounded-xl border border-stone-200 bg-white p-5">
        <h3 className="font-semibold text-stone-900">阶段耗时（已持久化）</h3>
        <p className="mt-1 text-sm text-stone-600">
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

function TokenBudgetAudit({ metrics }: { metrics: ProductRunMetrics }) {
  const observations = metrics.token_budget.cost_observations ?? [];
  const recorded = metrics.token_budget.cost_observation_count ?? observations.length;
  if (!observations.length) {
    return (
      <div className="rounded-xl border border-stone-200 bg-white p-5">
        <h3 className="font-semibold text-stone-900">Token Budget Audit</h3>
        <p className="mt-1 text-sm text-stone-600">
          当前运行尚无 Developer/Repair 已结算轮次。预算拒绝仍以失败证据中的 reservation
          事实为准。
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-semibold text-stone-900">Token Budget Audit</h3>
        <span className="text-xs text-stone-500">
          已结算 {recorded} 轮
          {metrics.token_budget.cost_observations_truncated ? " · 仅展示最近 256 轮" : ""}
        </span>
      </div>
      <p className="mt-1 text-sm text-stone-600">
        逐轮比较请求估算与供应商实际 Prompt。正偏差表示预算估算更保守，负偏差表示实际 Prompt
        超过请求估算；这里不伪造历史 Completion reservation。
      </p>
      <div className="mt-4 overflow-x-auto">
        <table className="min-w-full text-left text-xs text-stone-600">
          <thead className="border-b border-stone-200 text-stone-500">
            <tr>
              <th className="px-2 py-2 font-medium">任务 / 角色</th>
              <th className="px-2 py-2 font-medium">轮次</th>
              <th className="px-2 py-2 font-medium">请求估算</th>
              <th className="px-2 py-2 font-medium">实际 Prompt</th>
              <th className="px-2 py-2 font-medium">Completion</th>
              <th className="px-2 py-2 font-medium">估算偏差</th>
              <th className="px-2 py-2 font-medium">上下文增长</th>
              <th className="px-2 py-2 font-medium">Tool 参数 / 结果</th>
              <th className="px-2 py-2 font-medium">压缩写入</th>
              <th className="px-2 py-2 font-medium">代码进展</th>
            </tr>
          </thead>
          <tbody>
            {observations.map((item) => (
              <tr key={item.observation_id} className="border-b border-stone-100 last:border-0">
                <td className="whitespace-nowrap px-2 py-2">
                  <span className="font-mono text-stone-700">{item.task_id}</span>
                  <span className="ml-2 text-stone-400">{roleLabel(item.role)}</span>
                </td>
                <td className="px-2 py-2 font-mono">{item.iteration}</td>
                <td className="px-2 py-2 font-mono">{item.request_estimated_tokens.toLocaleString()}</td>
                <td className="px-2 py-2 font-mono">{item.actual_prompt_tokens.toLocaleString()}</td>
                <td className="px-2 py-2 font-mono">{item.actual_completion_tokens.toLocaleString()}</td>
                <td className="px-2 py-2 font-mono">{formatSigned(item.estimate_delta_tokens)}</td>
                <td className="px-2 py-2 font-mono">{item.context_growth_tokens.toLocaleString()}</td>
                <td className="px-2 py-2 font-mono">
                  {item.tool_argument_tokens.toLocaleString()} / {item.tool_result_tokens.toLocaleString()}
                </td>
                <td className="px-2 py-2 font-mono">
                  {item.compacted_tool_argument_tokens.toLocaleString()}
                </td>
                <td className="px-2 py-2">{item.has_real_progress ? "是" : "否"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-stone-500">
        Tool 参数中包含 write/apply_patch 参数的 Token 计数，但不包含源码或参数正文；“压缩写入”
        表示成功变更后从下一轮 Provider View 中移除的写入参数 Token。
      </p>
    </div>
  );
}

function formatSigned(value: number): string {
  return value > 0 ? `+${value.toLocaleString()}` : value.toLocaleString();
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
    <div className="rounded-xl border border-stone-200 bg-stone-50 p-4">
      <dt className="text-xs uppercase tracking-wide text-stone-500">{label}</dt>
      <dd className={`mt-2 text-xl text-stone-900 ${mono ? "font-mono" : ""}`}>{value}</dd>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <dt className="text-xs text-stone-500">{label}</dt>
      <dd className="mt-1 font-mono text-lg text-stone-800">{value}</dd>
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