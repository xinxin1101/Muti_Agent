import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  cleanupDependencyEnvironments,
  getDependencyEnvironment,
  getDependencyEnvironmentMetrics,
  rebuildDependencyEnvironment,
} from "../api/product";

type Props = Readonly<{ projectId: string }>;

export function DependencyEnvironmentPanel({ projectId }: Props) {
  const queryClient = useQueryClient();
  const environment = useQuery({
    queryKey: ["dependency-environment", projectId],
    queryFn: () => getDependencyEnvironment(projectId),
  });
  const metrics = useQuery({
    queryKey: ["dependency-environment-metrics"],
    queryFn: getDependencyEnvironmentMetrics,
  });
  const rebuild = useMutation({
    mutationFn: () => rebuildDependencyEnvironment(projectId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dependency-environment", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["dependency-environment-metrics"] });
    },
  });
  const cleanup = useMutation({
    mutationFn: cleanupDependencyEnvironments,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["dependency-environment", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["dependency-environment-metrics"] });
    },
  });

  if (environment.isLoading) return <p className="text-sm text-slate-500">正在读取验证环境状态…</p>;
  if (environment.error || !environment.data) {
    return <p className="rounded-xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm text-amber-100">验证环境状态不可用：{environment.error?.message ?? "未返回状态。"}</p>;
  }
  const status = environment.data;
  const cacheLabel = status.cache_state === "HIT" ? "缓存可复用" : status.cache_state === "MISS" ? "等待构建" : "无需缓存";

  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold text-white">准备验证环境</h2>
          <p className="mt-1 text-sm text-slate-400">{cacheLabel} · {status.package_manager} · 指纹 {status.dependency_fingerprint.slice(0, 12)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => rebuild.mutate()} disabled={rebuild.isPending} className="rounded-lg border border-cyan-300/40 bg-cyan-300/10 px-3 py-2 text-sm text-cyan-100 disabled:opacity-60">
            {rebuild.isPending ? "正在重新构建…" : "重新构建环境"}
          </button>
          <button type="button" onClick={() => cleanup.mutate()} disabled={cleanup.isPending} className="rounded-lg border border-slate-600 px-3 py-2 text-sm text-slate-200 disabled:opacity-60">
            {cleanup.isPending ? "正在清理…" : "清理旧缓存"}
          </button>
        </div>
      </div>
      <p className="mt-3 text-sm text-slate-300">构建耗时：{status.build_duration_ms === null ? "—" : `${status.build_duration_ms} ms`} · 产物大小：{formatBytes(status.artifact_bytes)}</p>
      {metrics.data ? <p className="mt-2 text-xs text-slate-500">缓存命中率 {(metrics.data.hit_rate * 100).toFixed(0)}% · 构建 {metrics.data.builds} 次 · 失败 {metrics.data.failures} 次 · 缓存总量 {formatBytes(metrics.data.cache_bytes)}</p> : null}
      <details className="mt-4 rounded-lg border border-slate-800 bg-slate-950/50 p-3">
        <summary className="cursor-pointer text-sm text-slate-200">查看依赖日志</summary>
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-400">{status.log_tail}</pre>
      </details>
      {rebuild.error instanceof Error ? <p className="mt-3 text-sm text-rose-200">重建失败：{rebuild.error.message}</p> : null}
      {cleanup.data ? <p className="mt-3 text-xs text-slate-500">已回收 {formatBytes(cleanup.data.reclaimed_bytes)}。</p> : null}
    </section>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
