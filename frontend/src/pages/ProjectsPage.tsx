import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";

import { createProject, listProjects } from "../api/product";

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const create = useMutation({
    mutationFn: createProject,
    onSuccess: async () => {
      setRepositoryUrl("");
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({
      repository_url: repositoryUrl,
      default_branch: defaultBranch,
    });
  }

  return (
    <section className="space-y-8">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
          Step 4.2
        </p>
        <h1 className="mt-2 text-4xl font-semibold text-white">Projects</h1>
        <p className="mt-3 max-w-3xl text-slate-400">
          Register a repository through the backend-managed workspace boundary.
          Browser-visible configuration never contains Git or provider credentials.
        </p>
      </div>

      <form
        onSubmit={submit}
        className="grid gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5 md:grid-cols-[1fr_12rem_auto]"
      >
        <label className="space-y-2 text-sm text-slate-300">
          <span>HTTPS repository URL</span>
          <input
            required
            type="url"
            value={repositoryUrl}
            onChange={(event) => setRepositoryUrl(event.target.value)}
            placeholder="https://github.com/org/repository"
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>
        <label className="space-y-2 text-sm text-slate-300">
          <span>Default branch</span>
          <input
            required
            value={defaultBranch}
            onChange={(event) => setDefaultBranch(event.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>
        <button
          type="submit"
          disabled={create.isPending}
          className="self-end rounded-md bg-cyan-300 px-4 py-2 font-semibold text-slate-950 disabled:opacity-50"
        >
          {create.isPending ? "Registering…" : "Register project"}
        </button>
        {create.error ? (
          <p className="text-sm text-rose-300 md:col-span-3">
            {create.error.message}
          </p>
        ) : null}
      </form>

      <div className="grid gap-4">
        {projects.isLoading ? <p className="text-slate-400">Loading projects…</p> : null}
        {projects.error ? (
          <p className="text-rose-300">{projects.error.message}</p>
        ) : null}
        {projects.data?.map((project) => (
          <article
            key={project.project_id}
            className="rounded-xl border border-slate-800 bg-slate-900/50 p-5"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="font-semibold text-white">
                  {project.repository_url}
                </h2>
                <p className="mt-1 text-sm text-slate-400">
                  {project.default_branch} · {project.run_count} runs · workspace{" "}
                  {project.workspace_ready ? "ready" : "not ready"}
                </p>
              </div>
              <div className="flex gap-2">
                <Link
                  to={`/runs?projectId=${project.project_id}`}
                  className="rounded-md border border-slate-700 px-3 py-2 text-sm"
                >
                  View runs
                </Link>
                <Link
                  to={`/runs/new?projectId=${project.project_id}`}
                  className="rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950"
                >
                  New run
                </Link>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
