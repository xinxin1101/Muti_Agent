import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { getTask, getTaskDiff } from "../api/product";
import { DiffViewer } from "../components/DiffViewer";
import { StatusBadge } from "../components/StatusBadge";
import type { ProductDiffKind } from "../types/product";
import { formatDateTime, labelFor, translateTaskObjective } from "../i18n";

export function TaskDetailPage() {
  const { runId = "", taskId = "" } = useParams();
  const [diffKind, setDiffKind] = useState<ProductDiffKind>("TASK");
  const task = useQuery({
    queryKey: ["task", runId, taskId],
    queryFn: () => getTask(runId, taskId),
    enabled: Boolean(runId && taskId),
  });
  const diff = useQuery({
    queryKey: ["task-diff", runId, taskId, diffKind],
    queryFn: () => getTaskDiff(runId, taskId, diffKind),
    enabled: Boolean(runId && taskId),
    retry: false,
  });

  if (task.isLoading) {
    return <p className="text-slate-400">正在加载任务…</p>;
  }
  if (task.error || !task.data) {
    return <p className="text-rose-300">{task.error?.message ?? "未找到任务。"}</p>;
  }

  const contract = task.data.task;
  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to={`/runs/${runId}`} className="text-sm text-cyan-300">
            ← 返回运行看板
          </Link>
          <h1 className="mt-3 font-mono text-3xl font-semibold text-white">
            {contract.task_id}
          </h1>
          <p className="mt-3 max-w-3xl text-slate-300">
            {translateTaskObjective(contract.objective)}
          </p>
        </div>
        <StatusBadge status={task.data.run_status} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ContractList title="可写范围" items={contract.writable_files} />
        <ContractList title="只读范围" items={contract.readonly_files} />
        <ContractList title="可读取范围" items={contract.readable_files} />
        <ContractList
          title="验收标准"
          items={contract.acceptance_criteria}
        />
        <ContractList
          title="验证命令"
          items={contract.verification_commands}
        />
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="font-semibold text-white">契约证据</h2>
          <p className="mt-3 break-all font-mono text-xs text-slate-400">
            SHA-256 {task.data.contract_sha256}
          </p>
          <p className="mt-3 text-sm text-slate-400">
            最大重试次数：{contract.max_retries}
          </p>
        </div>
      </div>

      <section className="space-y-4" aria-labelledby="diff-viewer-heading">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 id="diff-viewer-heading" className="text-xl font-semibold text-white">
              只读代码变更
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              提交对由后端持久化证据解析；浏览器不会提供 Git SHA。
            </p>
          </div>
          <div className="flex gap-2" aria-label="差异证据类型">
            {(["TASK", "INTEGRATION"] as const).map((kind) => (
              <button
                key={kind}
                type="button"
                onClick={() => setDiffKind(kind)}
                aria-pressed={diffKind === kind}
                className={`rounded-lg border px-3 py-2 text-xs font-semibold ${
                  diffKind === kind
                    ? "border-cyan-500 bg-cyan-500/10 text-cyan-200"
                    : "border-slate-700 text-slate-400"
                }`}
              >
                {kind === "TASK" ? "任务变更" : "集成变更"}
              </button>
            ))}
          </div>
        </div>

        {diff.isLoading ? <p className="text-slate-500">正在加载已验证的差异…</p> : null}
        {diff.error ? (
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">
            {diff.error.message}
          </div>
        ) : null}
        {diff.data ? <DiffViewer diff={diff.data} /> : null}
      </section>

      <div className="space-y-3">
        <h2 className="text-xl font-semibold text-white">证据记录</h2>
        {task.data.evidence.length === 0 ? (
          <p className="text-slate-500">尚未持久化任务证据。</p>
        ) : null}
        {task.data.evidence.map((evidence) => (
          <article
            key={evidence.evidence_id}
            className="rounded-xl border border-slate-800 bg-slate-900/50 p-4"
          >
            <div className="flex flex-wrap justify-between gap-3">
              <div>
                <p className="font-semibold text-slate-200">{labelFor(evidence.kind)}</p>
                <p className="mt-1 text-sm text-slate-500">
                  {evidence.stage ?? "无阶段"} · 序列{" "}
                  {evidence.sequence ?? "—"}
                </p>
              </div>
              <time className="text-xs text-slate-500">
                {formatDateTime(evidence.created_at)}
              </time>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

type ContractListProps = {
  title: string;
  items: readonly string[];
};

function ContractList({ title, items }: ContractListProps) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
      <h2 className="font-semibold text-white">{title}</h2>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">无</p>
      ) : (
        <ul className="mt-3 space-y-2 font-mono text-xs text-slate-300">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
