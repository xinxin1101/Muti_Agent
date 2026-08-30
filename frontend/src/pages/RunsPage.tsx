import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";

import { listRuns } from "../api/product";
import { StatusBadge } from "../components/StatusBadge";

export function RunsPage() {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get("projectId") ?? undefined;
  const runs = useQuery({
    queryKey: ["runs", projectId ?? "all"],
    queryFn: () => listRuns(projectId),
  });

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
            运行管理
          </p>
          <h1 className="mt-2 text-4xl font-semibold text-white">运行记录</h1>
          <p className="mt-3 text-slate-400">
            已持久化的运行状态来自后端事实，不会由浏览器状态推断。
          </p>
        </div>
        <Link
          to={projectId ? `/runs/new?projectId=${projectId}` : "/runs/new"}
          className="rounded-md bg-cyan-300 px-4 py-2 font-semibold text-slate-950"
        >
          新建运行
        </Link>
      </div>

      {runs.isLoading ? <p className="text-slate-400">正在加载运行记录…</p> : null}
      {runs.error ? <p className="text-rose-300">{runs.error.message}</p> : null}
      <div className="grid gap-4">
        {runs.data?.map((run) => (
          <Link
            key={run.run_id}
            to={`/runs/${run.run_id}`}
            className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 transition hover:border-slate-700"
          >
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="font-mono text-sm text-slate-300">
                  {run.run_id}
                </p>
                <p className="mt-2 text-sm text-slate-500">
                  {run.task_count} 个任务 · 基线 {run.base_commit.slice(0, 12)}
                </p>
              </div>
              <StatusBadge status={run.status} />
            </div>
          </Link>
        ))}
      </div>
    </section>
  );
}
