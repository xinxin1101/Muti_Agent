import type { ProductRunMetrics } from "../types/product";

export function RunMetrics({ metrics }: { metrics: ProductRunMetrics }) {
  return (
    <section className="space-y-4" aria-label="Run metrics">
      <div>
        <h2 className="text-xl font-semibold text-white">Run metrics</h2>
        <p className="mt-1 text-sm text-slate-500">
          Descriptive counters from accepted persistence and runtime events. Status remains sourced
          from {metrics.status_basis}; these numbers never authorize success.
        </p>
      </div>

      <dl className="grid gap-3 md:grid-cols-4">
        <MetricCard
          label="Terminal duration"
          value={formatDuration(metrics.terminal_duration_ms)}
        />
        <MetricCard label="Evidence records" value={String(metrics.evidence.total_records)} />
        <MetricCard label="Runtime events" value={String(metrics.runtime_events.total_events)} />
        <MetricCard
          label="Latest event sequence"
          value={`#${metrics.runtime_events.latest_sequence}`}
          mono
        />
      </dl>

      <div className="grid gap-4 xl:grid-cols-2">
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h3 className="font-semibold text-slate-200">Accepted work evidence</h3>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Metric label="Developer runs" value={metrics.evidence.developer_runs} />
            <Metric label="Verification attempts" value={metrics.evidence.verification_attempts} />
            <Metric label="Review decisions" value={metrics.evidence.review_decisions} />
            <Metric label="Repair attempts" value={metrics.evidence.repair_attempts} />
            <Metric label="Failure reports" value={metrics.evidence.failure_reports} />
            <Metric label="Worker executions" value={metrics.evidence.worker_executions} />
            <Metric label="Dispatch events" value={metrics.evidence.dispatch_events} />
            <Metric label="Merge conflicts" value={metrics.evidence.merge_conflicts} />
            <Metric label="Human decisions" value={metrics.evidence.human_decisions} />
          </dl>
        </div>

        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h3 className="font-semibold text-slate-200">Runtime observability</h3>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Metric label="Warning events" value={metrics.runtime_events.warning_events} />
            <Metric label="Error events" value={metrics.runtime_events.error_events} />
            <Metric label="Lease acquisitions" value={metrics.runtime_events.lease_acquisitions} />
            <Metric label="Lease takeovers" value={metrics.runtime_events.lease_takeovers} />
            <Metric label="Lease releases" value={metrics.runtime_events.lease_releases} />
            <Metric
              label="Integration gates"
              value={metrics.evidence.integration_gate_evaluations}
            />
            <Metric
              label="Merge snapshots"
              value={metrics.evidence.merge_queue_snapshots}
            />
          </dl>
        </div>
      </div>
    </section>
  );
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

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-mono text-lg text-slate-200">{value}</dd>
    </div>
  );
}

function formatDuration(value: number | null): string {
  if (value === null) {
    return "Pending terminal timestamp";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}
