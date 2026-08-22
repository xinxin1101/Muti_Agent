import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  executeOperatorAction,
  getOperatorRecovery,
  type OperatorAction,
} from "../api/product";

export function OperatorRecoveryPanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const recovery = useQuery({
    queryKey: ["operator-recovery", runId],
    queryFn: () => getOperatorRecovery(runId),
    enabled: Boolean(runId),
  });
  const action = useMutation({
    mutationFn: (selected: OperatorAction) => executeOperatorAction(runId, selected.action_id),
    onSuccess: async (result) => {
      queryClient.setQueryData(["operator-recovery", runId], result.refreshed_plan);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["operator-recovery", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-dag", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-metrics", runId] }),
        queryClient.invalidateQueries({ queryKey: ["human-gates", runId] }),
      ]);
    },
  });

  if (recovery.isLoading) {
    return (
      <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
        Reconstructing durable recovery state…
      </p>
    );
  }
  if (recovery.error || !recovery.data) {
    return (
      <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
        <h2 className="font-semibold text-amber-100">Operator recovery unavailable</h2>
        <p className="mt-2 text-sm text-amber-200/80">
          {recovery.error?.message ?? "Durable recovery state could not be reconstructed."}
        </p>
      </div>
    );
  }

  const plan = recovery.data;
  return (
    <section aria-label="Operator recovery" className="space-y-4">
      <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/5 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold text-cyan-50">Operator recovery</h2>
            <p className="mt-2 max-w-3xl text-sm text-cyan-100/75">
              This is a diagnostic projection over durable runtime facts. Causal trace can explain
              the Run, but it cannot authorize retry, resume, merge, or publication.
            </p>
          </div>
          <span className="rounded-full border border-cyan-300/30 px-3 py-1 font-mono text-xs text-cyan-100">
            FRESH REVALIDATION REQUIRED
          </span>
        </div>

        <div className="mt-5 space-y-2">
          {plan.reconciliation.tasks.map((task) => (
            <div
              key={task.task_id}
              className="grid gap-2 rounded-lg border border-slate-800 bg-slate-950/50 p-3 md:grid-cols-[8rem_12rem_1fr]"
            >
              <p className="font-mono text-xs text-cyan-200">{task.task_id}</p>
              <div>
                <p className="font-mono text-xs text-slate-300">{task.frontier_state}</p>
                <p className="mt-1 text-[11px] text-slate-600">
                  {task.lease_state} · gen {task.lease_generation}
                </p>
              </div>
              <p className="text-xs leading-5 text-slate-400">{task.reason}</p>
            </div>
          ))}
        </div>

        {action.error ? (
          <p className="mt-4 rounded-md border border-rose-400/20 bg-rose-400/5 p-3 text-sm text-rose-200">
            {action.error.message}
          </p>
        ) : null}

        <div className="mt-5 flex flex-wrap gap-3">
          {plan.actions.map((candidate) => (
            <button
              key={candidate.action_id}
              type="button"
              disabled={action.isPending}
              title={candidate.description}
              onClick={() => action.mutate(candidate)}
              className="rounded-md bg-cyan-200 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {action.isPending ? "Revalidating durable facts…" : candidate.label}
            </button>
          ))}
          {plan.actions.length === 0 ? (
            <p className="text-sm text-slate-500">
              No operator mutation is currently advertised by the server. The browser will not
              synthesize one from trace or task state.
            </p>
          ) : null}
        </div>

        <p className="mt-4 text-[11px] text-slate-600">
          Action identities are opaque server values bound to current DAG, lease, worker evidence,
          execution-base and dispatch-ledger facts. Clicking sends only that action id.
        </p>
      </div>
    </section>
  );
}
