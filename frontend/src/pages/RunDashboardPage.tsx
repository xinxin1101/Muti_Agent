import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router";

import { getRun } from "../api/product";
import { StatusBadge } from "../components/StatusBadge";
import type { RunLaunchResponse } from "../types/product";

type LaunchState = Readonly<{
  launch?: RunLaunchResponse;
}>;

export function RunDashboardPage() {
  const { runId = "" } = useParams();
  const location = useLocation();
  const launch = (location.state as LaunchState | null)?.launch;
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    enabled: Boolean(runId),
  });

  if (run.isLoading) {
    return <p className="text-slate-400">Loading run…</p>;
  }
  if (run.error || !run.data) {
    return <p className="text-rose-300">{run.error?.message ?? "Run not found."}</p>;
  }

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-sm text-cyan-300">{run.data.run_id}</p>
          <h1 className="mt-2 text-4xl font-semibold text-white">Run Dashboard</h1>
          <p className="mt-3 text-slate-400">
            {run.data.repository_url} · {run.data.default_branch}
          </p>
        </div>
        <StatusBadge status={run.data.status} />
      </div>

      {launch ? (
        <div
          className={[
            "rounded-xl border p-4 text-sm",
            launch.dispatch_status === "QUEUED"
              ? "border-emerald-400/20 bg-emerald-400/5 text-emerald-200"
              : "border-amber-400/20 bg-amber-400/5 text-amber-100",
          ].join(" ")}
        >
          Dispatch: {launch.dispatch_status}
          {launch.detail ? ` · ${launch.detail}` : ""}
        </div>
      ) : null}

      <dl className="grid gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5 md:grid-cols-3">
        <Metric label="Base commit" value={run.data.base_commit.slice(0, 12)} mono />
        <Metric label="Tasks" value={String(run.data.task_count)} />
        <Metric label="Started" value={new Date(run.data.started_at).toLocaleString()} />
      </dl>

      <div className="space-y-3">
        <h2 className="text-xl font-semibold text-white">Tasks</h2>
        {run.data.tasks.map((task) => (
          <Link
            key={task.task_id}
            to={`/runs/${run.data.run_id}/tasks/${encodeURIComponent(task.task_id)}`}
            className="block rounded-xl border border-slate-800 bg-slate-900/50 p-5 transition hover:border-slate-700"
          >
            <div className="flex flex-wrap justify-between gap-4">
              <div>
                <p className="font-mono text-sm text-cyan-200">{task.task_id}</p>
                <p className="mt-2 text-slate-300">{task.objective}</p>
              </div>
              <p className="text-sm text-slate-500">
                {task.evidence_count} evidence records
              </p>
            </div>
          </Link>
        ))}
      </div>
    </section>
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
