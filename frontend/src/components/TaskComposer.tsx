import { useMutation, useQuery } from "@tanstack/react-query";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { createRequirementRun, listProjects } from "../api/product";

type Props = Readonly<{ initialProjectId?: string; compact?: boolean }>;

export function TaskComposer({ initialProjectId = "", compact = false }: Props) {
  const navigate = useNavigate();
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const [projectId, setProjectId] = useState(initialProjectId);
  const [requirement, setRequirement] = useState("");

  useEffect(() => setProjectId(initialProjectId), [initialProjectId]);
  const effectiveProjectId = useMemo(() => projectId || projects.data?.[0]?.project_id || "", [projectId, projects.data]);
  const selectedProject = projects.data?.find((project) => project.project_id === effectiveProjectId);
  const launch = useMutation({
    mutationFn: createRequirementRun,
    onSuccess: (result) => void navigate(`/runs/${result.run_id}`, { state: { launch: result } }),
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Guard the event handler as well as the disabled button: two clicks can arrive before
    // React commits the pending mutation state, and creating two Planner launches would consume
    // two independent planning budgets for the same user request.
    if (launch.isPending || !effectiveProjectId || !requirement.trim()) return;
    launch.mutate({ project_id: effectiveProjectId, requirement: requirement.trim() });
  }

  return (
    <form onSubmit={submit} className={`task-composer w-full rounded-2xl border border-stone-200 bg-white p-4 shadow-[0_12px_38px_rgba(41,37,36,0.08)] ${compact ? "" : "max-w-6xl"}`}>
      <div className="flex flex-wrap items-center gap-2 px-2 py-1 text-xs text-stone-500">
        <label className="inline-flex items-center gap-2">
          <span className="sr-only">项目</span>
          <select aria-label="项目" required value={effectiveProjectId} onChange={(event) => setProjectId(event.target.value)} className="max-w-64 rounded-lg border border-stone-200 bg-stone-50 px-2.5 py-1.5 text-sm text-stone-700 outline-none focus:border-blue-400">
            <option value="">请选择项目</option>
            {projects.data?.map((project) => <option key={project.project_id} value={project.project_id}>{project.repository_url}</option>)}
          </select>
        </label>
        {selectedProject ? <span className="rounded-md bg-emerald-50 px-2 py-1 text-emerald-700">{selectedProject.default_branch} · 已连接</span> : null}
      </div>
      <textarea required aria-label="需求描述" rows={compact ? 4 : 5} maxLength={12000} value={requirement} onChange={(event) => setRequirement(event.target.value)} placeholder="描述你的需求，例如：实现一个五子棋网页游戏，并提交到分支…" className="block w-full resize-none border-0 bg-transparent px-2 py-4 text-[15px] leading-7 text-stone-800 outline-none placeholder:text-stone-400" />
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-stone-100 px-2 pt-3">
        <div className="flex flex-wrap gap-1.5 text-xs text-stone-500">
          <span className="rounded-full bg-stone-100 px-2.5 py-1">自动拆分任务</span>
          <span className="rounded-full bg-stone-100 px-2.5 py-1">运行验证</span>
          <span className="rounded-full bg-stone-100 px-2.5 py-1">成功后可创建草稿 PR</span>
        </div>
        <button type="submit" aria-label="启动多智能体运行" disabled={launch.isPending || !effectiveProjectId || !requirement.trim()} className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45">
          {launch.isPending ? "正在准备…" : "开始开发 ↑"}
        </button>
      </div>
      {launch.error instanceof Error ? <p className="px-2 pt-3 text-sm text-rose-600">{launch.error.message}</p> : null}
    </form>
  );
}
