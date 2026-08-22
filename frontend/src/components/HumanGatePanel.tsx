import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  decideHumanGate,
  listHumanGates,
  type HumanGateDecisionName,
  type HumanGateSnapshot,
} from "../api/product";

export function HumanGatePanel({ runId }: { runId: string }) {
  const queryClient = useQueryClient();
  const gates = useQuery({
    queryKey: ["human-gates", runId],
    queryFn: () => listHumanGates(runId),
    enabled: Boolean(runId),
  });
  const decision = useMutation({
    mutationFn: ({
      gate,
      action,
      note,
    }: {
      gate: HumanGateSnapshot;
      action: HumanGateDecisionName;
      note: string;
    }) =>
      decideHumanGate(runId, gate.task_id, {
        evidence_fingerprint: gate.evidence_fingerprint,
        decision: action,
        note,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["human-gates", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-dag", runId] }),
        queryClient.invalidateQueries({ queryKey: ["run-metrics", runId] }),
      ]);
    },
  });

  if (gates.isLoading) {
    return (
      <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
        Checking durable human gates…
      </p>
    );
  }
  if (gates.error) {
    return (
      <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
        <h2 className="font-semibold text-amber-100">Human Gate unavailable</h2>
        <p className="mt-2 text-sm text-amber-200/80">{gates.error.message}</p>
      </div>
    );
  }
  if (!gates.data?.length) {
    return null;
  }

  return (
    <section aria-label="Durable human gates" className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold text-white">Human intervention</h2>
        <p className="mt-1 text-sm text-slate-500">
          Decisions are bound to persisted conflict evidence and revalidated Git state. The browser
          cannot supply branches, commits, lease generations, or run tokens.
        </p>
      </div>
      {gates.data.map((gate) => (
        <HumanGateCard
          key={gate.evidence_fingerprint}
          gate={gate}
          pending={decision.isPending}
          error={decision.error?.message ?? null}
          onDecision={(action, note) => decision.mutate({ gate, action, note })}
        />
      ))}
    </section>
  );
}

function HumanGateCard({
  gate,
  pending,
  error,
  onDecision,
}: {
  gate: HumanGateSnapshot;
  pending: boolean;
  error: string | null;
  onDecision: (action: HumanGateDecisionName, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const awaiting = gate.state === "AWAITING_HUMAN";

  return (
    <article className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-sm text-amber-200">{gate.task_id}</p>
          <h3 className="mt-1 font-semibold text-amber-50">Integration conflict</h3>
        </div>
        <span className="rounded-full border border-amber-300/30 px-3 py-1 font-mono text-xs text-amber-100">
          {gate.state}
        </span>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <p className="text-xs uppercase tracking-wide text-amber-200/60">Conflicting paths</p>
          <ul className="mt-2 space-y-1 font-mono text-xs text-amber-100/90">
            {gate.policy.conflicting_paths.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-amber-200/60">Policy evidence</p>
          <ul className="mt-2 space-y-1 text-xs text-amber-100/80">
            {gate.policy.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-4 break-all font-mono text-[11px] text-slate-500">
        Evidence {gate.evidence_fingerprint}
      </p>

      {awaiting ? (
        <div className="mt-5 space-y-3">
          <label className="block text-sm text-slate-300">
            <span>Decision note (optional)</span>
            <input
              aria-label={`Decision note for ${gate.task_id}`}
              value={note}
              maxLength={512}
              onChange={(event) => setNote(event.target.value.replace(/[\r\n]+/g, " "))}
              className="mt-2 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            />
          </label>
          {error ? <p className="text-sm text-rose-300">{error}</p> : null}
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={pending || !gate.policy.human_repair_authorizable}
              onClick={() => onDecision("AUTHORIZE_REPAIR", note.trim())}
              className="rounded-md bg-amber-200 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {pending ? "Recording decision…" : "Authorize bounded repair"}
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => onDecision("ABORT", note.trim())}
              className="rounded-md border border-rose-400/40 px-4 py-2 text-sm font-semibold text-rose-200 disabled:opacity-40"
            >
              Abort Run
            </button>
          </div>
          {!gate.policy.human_repair_authorizable ? (
            <p className="text-xs text-rose-200/80">
              Policy hard boundaries prohibit Agent repair for this conflict. Abort remains the only
              browser-authorized action.
            </p>
          ) : null}
        </div>
      ) : (
        <p className="mt-5 text-sm text-amber-100/80">
          {gate.state === "REPAIR_AUTHORIZED"
            ? "Repair authorization is durable. The server must revalidate the same Git and policy facts before any integration repair can run."
            : gate.state === "ABORTED"
              ? "This conflict was explicitly aborted and cannot be resumed as repair authorization."
              : "This gate is not awaiting a browser decision."}
        </p>
      )}
    </article>
  );
}
