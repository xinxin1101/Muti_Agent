import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { createRun, listProjects } from "../api/product";

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function NewRunPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const initialProjectId = searchParams.get("projectId") ?? "";
  const [projectId, setProjectId] = useState(initialProjectId);
  const [taskId, setTaskId] = useState("task-1");
  const [objective, setObjective] = useState("");
  const [readableFiles, setReadableFiles] = useState("");
  const [writableFiles, setWritableFiles] = useState("");
  const [readonlyFiles, setReadonlyFiles] = useState("");
  const [criteria, setCriteria] = useState("");
  const [commands, setCommands] = useState("pytest -q");
  const [maxRetries, setMaxRetries] = useState(2);

  const effectiveProjectId = useMemo(() => {
    if (projectId) {
      return projectId;
    }
    return projects.data?.[0]?.project_id ?? "";
  }, [projectId, projects.data]);

  const launch = useMutation({
    mutationFn: createRun,
    onSuccess: (result) => {
      navigate(`/runs/${result.run_id}`, { state: { launch: result } });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    launch.mutate({
      project_id: effectiveProjectId,
      task: {
        task_id: taskId,
        objective,
        readable_files: splitLines(readableFiles),
        writable_files: splitLines(writableFiles),
        readonly_files: splitLines(readonlyFiles),
        acceptance_criteria: splitLines(criteria),
        verification_commands: splitLines(commands),
        max_retries: maxRetries,
      },
    });
  }

  return (
    <section className="space-y-8">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
          Step 4.2
        </p>
        <h1 className="mt-2 text-4xl font-semibold text-white">New Run</h1>
        <p className="mt-3 max-w-3xl text-slate-400">
          The backend derives the exact base commit from the managed Git workspace.
          The browser submits a validated TaskContract and never chooses repository truth.
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

        <div className="grid gap-4 md:grid-cols-2">
          <label className="space-y-2 text-sm text-slate-300">
            <span>Task ID</span>
            <input
              required
              value={taskId}
              onChange={(event) => setTaskId(event.target.value)}
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
            />
          </label>
          <label className="space-y-2 text-sm text-slate-300">
            <span>Max retries</span>
            <input
              type="number"
              min={0}
              max={5}
              value={maxRetries}
              onChange={(event) => setMaxRetries(Number(event.target.value))}
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
            />
          </label>
        </div>

        <label className="space-y-2 text-sm text-slate-300">
          <span>Objective</span>
          <textarea
            required
            rows={4}
            value={objective}
            onChange={(event) => setObjective(event.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>

        <div className="grid gap-4 md:grid-cols-3">
          <LineListField
            label="Readable files"
            value={readableFiles}
            onChange={setReadableFiles}
          />
          <LineListField
            label="Writable files"
            value={writableFiles}
            onChange={setWritableFiles}
            required
          />
          <LineListField
            label="Read-only files"
            value={readonlyFiles}
            onChange={setReadonlyFiles}
          />
        </div>

        <LineListField
          label="Acceptance criteria"
          value={criteria}
          onChange={setCriteria}
          required
        />
        <LineListField
          label="Verification commands"
          value={commands}
          onChange={setCommands}
          required
        />

        {launch.error ? (
          <p className="text-sm text-rose-300">{launch.error.message}</p>
        ) : null}

        <button
          type="submit"
          disabled={launch.isPending || !effectiveProjectId}
          className="w-fit rounded-md bg-cyan-300 px-5 py-2.5 font-semibold text-slate-950 disabled:opacity-50"
        >
          {launch.isPending ? "Launching…" : "Start run"}
        </button>
      </form>
    </section>
  );
}

type LineListFieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
};

function LineListField({
  label,
  value,
  onChange,
  required = false,
}: LineListFieldProps) {
  return (
    <label className="space-y-2 text-sm text-slate-300">
      <span>{label} · one per line</span>
      <textarea
        required={required}
        rows={4}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs"
      />
    </label>
  );
}
