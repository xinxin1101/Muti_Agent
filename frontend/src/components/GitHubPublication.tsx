import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getGitHubPublication, publishGitHubDraft } from "../api/product";
import type { ProductRun } from "../types/product";

export function GitHubPublication({
  runId,
  runStatus,
}: {
  runId: string;
  runStatus: ProductRun["status"];
}) {
  const queryClient = useQueryClient();
  const publication = useQuery({
    queryKey: ["github-publication", runId],
    queryFn: () => getGitHubPublication(runId),
    enabled: Boolean(runId) && runStatus === "SUCCEEDED",
    refetchInterval: (query) =>
      query.state.data?.state === "PUBLISHING" ? 2_000 : false,
  });
  const publish = useMutation({
    mutationFn: () => publishGitHubDraft(runId),
    onSuccess: (result) => {
      queryClient.setQueryData(["github-publication", runId], result);
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: ["github-publication", runId] });
    },
  });

  if (runStatus !== "SUCCEEDED") {
    return (
      <section
        className="rounded-xl border border-slate-800 bg-slate-900/50 p-5"
        aria-label="GitHub publication"
      >
        <h2 className="text-xl font-semibold text-white">GitHub publication</h2>
        <p className="mt-2 text-sm text-slate-400">
          Draft PR publication becomes eligible only after persisted Run status is SUCCEEDED.
        </p>
        <p className="mt-2 text-xs text-slate-500">
          GitHub state never promotes or overrides the Run status.
        </p>
      </section>
    );
  }

  if (publication.isLoading) {
    return (
      <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
        Loading GitHub publication eligibility…
      </p>
    );
  }

  if (publication.error || !publication.data) {
    return (
      <section
        className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5"
        aria-label="GitHub publication"
      >
        <h2 className="font-semibold text-amber-100">GitHub publication unavailable</h2>
        <p className="mt-2 text-sm text-amber-200/80">
          {publication.error?.message ??
            "Accepted runtime evidence does not define a publishable source."}
        </p>
      </section>
    );
  }

  const item = publication.data;
  const publishing = item.state === "PUBLISHING";
  return (
    <section
      className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5"
      aria-label="GitHub publication"
    >
      <div>
        <h2 className="text-xl font-semibold text-white">GitHub publication</h2>
        <p className="mt-1 text-sm text-slate-500">
          Publishes the backend-selected accepted commit to a DevFlow-owned branch and Draft PR.
          GitHub remains non-authoritative for Run success.
        </p>
      </div>

      <dl className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Fact label="State" value={item.state} />
        <Fact label="Source basis" value={item.source_basis} />
        <Fact label="Source commit" value={item.source_commit.slice(0, 12)} mono />
        <Fact label="Evidence" value={`#${item.source_evidence_id}`} mono />
        <Fact label="Repository" value={item.repository_slug} />
        <Fact label="Base branch" value={item.base_branch} mono />
        <Fact label="DevFlow branch" value={item.branch_name} mono />
        <Fact label="Attempts" value={String(item.attempt_count)} />
      </dl>

      {item.last_error_message ? (
        <p className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3 text-sm text-rose-200">
          {item.last_error_code ? `${item.last_error_code}: ` : ""}
          {item.last_error_message}
        </p>
      ) : null}
      {publish.error ? (
        <p className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3 text-sm text-rose-200">
          {publish.error.message}
        </p>
      ) : null}

      {item.pull_request_url ? (
        <a
          href={item.pull_request_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex rounded-lg border border-cyan-400/30 px-4 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-400/10"
        >
          Open Draft PR #{item.pull_request_number}
        </a>
      ) : (
        <button
          type="button"
          disabled={!item.publisher_configured || publish.isPending}
          onClick={() => publish.mutate()}
          className="rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {publish.isPending
            ? "Publishing…"
            : publishing
              ? "Retry publication"
              : "Create Draft PR"}
        </button>
      )}

      {publishing ? (
        <p className="text-xs text-slate-500">
          A backend publication claim exists. Retrying is safe: the backend rejects a still-live
          claim and takes over only after its PostgreSQL expiry.
        </p>
      ) : null}
      {!item.publisher_configured && !item.pull_request_url ? (
        <p className="text-xs text-amber-200/70">
          Backend GitHub publication credential is not configured. No credential is accepted from
          the browser.
        </p>
      ) : null}
    </section>
  );
}

function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`mt-1 break-all text-sm text-slate-200 ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
