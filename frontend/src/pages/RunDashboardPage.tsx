import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router";

import { getRun, getRunDAG, getRunMetrics } from "../api/product";
import {
  parseRuntimeEventSummary,
  runtimeEventStreamUrl,
} from "../api/runtime-events";
import { RunDAG } from "../components/RunDAG";
import { RunMetrics } from "../components/RunMetrics";
import { StatusBadge } from "../components/StatusBadge";
import type { RunLaunchResponse } from "../types/product";
import type { RuntimeEventSummary } from "../types/runtime";

type LaunchState = Readonly<{
  launch?: RunLaunchResponse;
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
  const [events, setEvents] = useState<readonly RuntimeEventSummary[]>([]);
  const [streamStatus, setStreamStatus] =
    useState<StreamStatus>("connecting");
  const [streamError, setStreamError] = useState<string | null>(null);
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
          throw new Error("Runtime event belongs to a different Run.");
        }

        if (event.sequence <= lastSequenceRef.current) {
          if (
            event.sequence === lastSequenceRef.current &&
            event.event_id === lastEventIdRef.current
          ) {
            return;
          }
          throw new Error(
            "Runtime event sequence moved backward or was reused for a different event.",
          );
        }
        if (
          lastSequenceRef.current > 0 &&
          event.sequence !== lastSequenceRef.current + 1
        ) {
          throw new Error("Runtime event sequence contains an unexpected gap.");
        }
        if (lastSequenceRef.current === 0 && event.sequence !== 1) {
          throw new Error("Runtime event stream did not start at sequence 1.");
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
          event.kind === "RUN_FINALIZED"
        ) {
          void queryClient.invalidateQueries({ queryKey: ["run", runId] });
          void queryClient.invalidateQueries({ queryKey: ["run-dag", runId] });
        }
      } catch (error) {
        source.close();
        setStreamStatus("error");
        setStreamError(
          error instanceof Error
            ? error.message
            : "Runtime event stream failed validation.",
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

      {metrics.isLoading ? (
        <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
          Loading accepted Run Metrics…
        </p>
      ) : metrics.error || !metrics.data ? (
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
          <h2 className="font-semibold text-amber-100">Run Metrics unavailable</h2>
          <p className="mt-2 text-sm text-amber-200/80">
            {metrics.error?.message ?? "A complete bounded metrics projection is unavailable."}
          </p>
          <p className="mt-2 text-xs text-amber-200/60">
            The browser will not infer partial counters or derive Run success from missing metrics.
          </p>
        </div>
      ) : (
        <RunMetrics metrics={metrics.data} />
      )}

      {dag.isLoading ? (
        <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
          Loading validated DAG…
        </p>
      ) : dag.error || !dag.data ? (
        <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
          <h2 className="font-semibold text-amber-100">DAG unavailable</h2>
          <p className="mt-2 text-sm text-amber-200/80">
            {dag.error?.message ?? "Validated topology is unavailable for this Run."}
          </p>
          <p className="mt-2 text-xs text-amber-200/60">
            The browser will not infer missing dependency edges from task order.
          </p>
        </div>
      ) : (
        <RunDAG runId={runId} dag={dag.data} />
      )}

      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-white">Live runtime timeline</h2>
            <p className="mt-1 text-sm text-slate-500">
              SSE observes accepted runtime events. Run success still comes from typed persistence
              and deterministic evidence gates.
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
                ? "This browser does not expose EventSource."
                : "Waiting for accepted runtime events…"}
            </p>
          ) : (
            <ol className="divide-y divide-slate-800">
              {events.map((event) => (
                <li key={event.event_id} className="grid gap-3 p-4 md:grid-cols-[5rem_11rem_1fr]">
                  <p className="font-mono text-xs text-slate-500">#{event.sequence}</p>
                  <div className="space-y-1">
                    <p className={`text-xs font-semibold ${levelClass(event.level)}`}>
                      {event.level}
                    </p>
                    <p className="text-xs text-slate-500">{event.source}</p>
                  </div>
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-mono text-xs text-cyan-200">{event.kind}</p>
                      {event.task_id ? (
                        <span className="text-xs text-slate-500">task {event.task_id}</span>
                      ) : null}
                      {event.generation ? (
                        <span className="text-xs text-slate-500">gen {event.generation}</span>
                      ) : null}
                    </div>
                    <p className="mt-2 text-sm text-slate-300">{event.message}</p>
                    <p className="mt-2 text-xs text-slate-600">
                      {new Date(event.created_at).toLocaleString()}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>

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

function StreamBadge({
  status,
  sequence,
}: {
  status: StreamStatus;
  sequence: number;
}) {
  const label = {
    connecting: "CONNECTING",
    live: "LIVE",
    reconnecting: "RECONNECTING",
    unsupported: "UNSUPPORTED",
    error: "STREAM ERROR",
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
