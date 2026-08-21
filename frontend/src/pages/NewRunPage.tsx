import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { createRequirementRun, listProjects } from "../api/product";

export function NewRunPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const initialProjectId = searchParams.get("projectId") ?? "";
  const [projectId, setProjectId] = useState(initialProjectId);
  const [requirement, setRequirement] = useState("");

  const effectiveProjectId = useMemo(() => {
    if (projectId) {
      return projectId;
    }
    return projects.data?.[0]?.project_id ?? "";
  }, [projectId, projects.data]);

  const launch = useMutation({
    mutationFn: createRequirementRun,
    onSuccess: (result) => {
      navigate(`/runs/${result.run_id}`, { state: { launch: result } });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedRequirement = requirement.trim();
    if (!effectiveProjectId || !normalizedRequirement) {
      return;
    }
    launch.mutate({
      project_id: effectiveProjectId,
      requirement: normalizedRequirement,
    });
  }

  return (
    <section className="space-y-8">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
          Phase 6 · Autonomous Multi-Agent
        </p>
        <h1 className="mt-2 text-4xl font-semibold text-white">New Run</h1>
        <p className="mt-3 max-w-3xl text-slate-400">
          Describe the repository change you want. DevFlow derives the exact Git base,
          asks the Planner for a validated task DAG, persists that DAG, and launches only
          its dependency-ready root tasks. The browser never authors TaskContracts,
          dependency edges, Git SHAs, or worker authority.
        </p>
      </div>

      <form
        onSubmit={submit}
        className="grid gap-5 rounded-xl border border-slate-800 bg-slate-900/50 p-6"
      >
        <label className="space-y-2 text-sm text-slate-300">
          <span>Project</span>
          <select
            required
            aria-label="Project"
            value={effectiveProjectId}
            onChange={(event) => setProjectId(event.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          >
            <option value="">Select a project</option>
            {projects.data?.map((project) => (
              <option key={project.project_id} value={project.project_id}>
                {project.repository_url}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-2 text-sm text-slate-300">
          <span>Requirement</span>
          <textarea
            required
            aria-label="Requirement"
            rows={9}
            maxLength={12000}
            value={requirement}
            onChange={(event) => setRequirement(event.target.value)}
            placeholder={
              "Example: Add JWT login with access and refresh tokens, add deterministic tests, " +
              "and do not modify the payments module."
            }
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-3 text-sm leading-6"
          />
          <span className="block text-xs text-slate-500">
            Planner output is validated server-side before any task is dispatched.
          </span>
        </label>

        {launch.error ? (
          <p className="text-sm text-rose-300">{launch.error.message}</p>
        ) : null}

        <button
          type="submit"
          disabled={launch.isPending || !effectiveProjectId || !requirement.trim()}
          className="w-fit rounded-md bg-cyan-300 px-5 py-2.5 font-semibold text-slate-950 disabled:opacity-50"
        >
          {launch.isPending ? "Planning and launching…" : "Start Multi-Agent run"}
        </button>
      </form>
    </section>
  );
}
