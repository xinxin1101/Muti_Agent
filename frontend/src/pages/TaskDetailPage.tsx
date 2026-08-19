import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";

import { getTask } from "../api/product";
import { StatusBadge } from "../components/StatusBadge";

export function TaskDetailPage() {
  const { runId = "", taskId = "" } = useParams();
  const task = useQuery({
    queryKey: ["task", runId, taskId],
    queryFn: () => getTask(runId, taskId),
    enabled: Boolean(runId && taskId),
  });

  if (task.isLoading) {
    return <p className="text-slate-400">Loading task…</p>;
  }
  if (task.error || !task.data) {
    return <p className="text-rose-300">{task.error?.message ?? "Task not found."}</p>;
  }

  const contract = task.data.task;
  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to={`/runs/${runId}`} className="text-sm text-cyan-300">
            ← Run dashboard
          </Link>
          <h1 className="mt-3 font-mono text-3xl font-semibold text-white">
            {contract.task_id}
          </h1>
          <p className="mt-3 max-w-3xl text-slate-300">{contract.objective}</p>
        </div>
        <StatusBadge status={task.data.run_status} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <ContractList title="Writable scope" items={contract.writable_files} />
        <ContractList title="Read-only scope" items={contract.readonly_files} />
        <ContractList title="Readable scope" items={contract.readable_files} />
        <ContractList
          title="Acceptance criteria"
          items={contract.acceptance_criteria}
        />
        <ContractList
          title="Verification commands"
          items={contract.verification_commands}
        />
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="font-semibold text-white">Contract evidence</h2>
          <p className="mt-3 break-all font-mono text-xs text-slate-400">
            SHA-256 {task.data.contract_sha256}
          </p>
          <p className="mt-3 text-sm text-slate-400">
            Max retries: {contract.max_retries}
          </p>
        </div>
      </div>

      <div className="space-y-3">
        <h2 className="text-xl font-semibold text-white">Evidence records</h2>
        {task.data.evidence.length === 0 ? (
          <p className="text-slate-500">No task evidence has been persisted yet.</p>
        ) : null}
        {task.data.evidence.map((evidence) => (
          <article
            key={evidence.evidence_id}
            className="rounded-xl border border-slate-800 bg-slate-900/50 p-4"
          >
            <div className="flex flex-wrap justify-between gap-3">
              <div>
                <p className="font-semibold text-slate-200">{evidence.kind}</p>
                <p className="mt-1 text-sm text-slate-500">
                  {evidence.stage ?? "no stage"} · sequence{" "}
                  {evidence.sequence ?? "—"}
                </p>
              </div>
              <time className="text-xs text-slate-500">
                {new Date(evidence.created_at).toLocaleString()}
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
        <p className="mt-3 text-sm text-slate-500">None</p>
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
