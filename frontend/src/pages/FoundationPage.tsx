const boundaries = [
  {
    title: "Server truth remains authoritative",
    body: "Frontend state is presentation state. Scheduling, leases, fencing, verification, and success decisions stay in the accepted backend runtime.",
  },
  {
    title: "Product requests are typed",
    body: "Step 4.2 adds bounded Project and Run HTTP contracts. Git HEAD and persisted runtime evidence remain backend-owned.",
  },
  {
    title: "Secrets stay out of the browser",
    body: "Provider credentials, GitHub credentials, database credentials, and run_token are never exposed through VITE_* configuration or client models.",
  },
] as const;

export function FoundationPage() {
  return (
    <section className="space-y-8">
      <div className="max-w-3xl space-y-4">
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
          Phase 4
        </p>
        <h1 className="text-4xl font-semibold tracking-tight text-white">
          DevFlow product foundation
        </h1>
        <p className="text-lg leading-8 text-slate-300">
          The React foundation now hosts bounded Project and Run product pages
          without widening the accepted runtime authority boundary.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {boundaries.map((boundary) => (
          <article
            key={boundary.title}
            className="rounded-xl border border-slate-800 bg-slate-900/60 p-5"
          >
            <h2 className="font-semibold text-white">{boundary.title}</h2>
            <p className="mt-3 text-sm leading-6 text-slate-400">
              {boundary.body}
            </p>
          </article>
        ))}
      </div>

      <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-5">
        <p className="text-sm font-medium text-emerald-200">
          Current product scope
        </p>
        <p className="mt-2 text-sm leading-6 text-slate-300">
          Projects, New Run, persisted Run Dashboard, and Task Detail are in
          scope. SSE, DAG visualization, diff viewing, metrics, and GitHub
          publication remain dedicated later steps.
        </p>
      </div>
    </section>
  );
}
