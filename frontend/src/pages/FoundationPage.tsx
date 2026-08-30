import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { TaskComposer } from "../components/TaskComposer";
import { listProjects } from "../api/product";

export function FoundationPage() {
  return (
    <section className="workspace-home mx-auto flex min-h-[calc(100vh-4rem)] w-full max-w-6xl flex-col items-center justify-start pb-16 pt-[clamp(4.5rem,11vh,9rem)]">
      <div className="mb-9 text-center">
        <h2 className="sr-only">DevFlow 产品概览</h2>
        <p className="text-sm text-stone-500">DevFlow 开发工作区</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-stone-900 md:text-4xl">下午好，准备开发什么？</h1>
        <p className="mx-auto mt-3 max-w-xl text-[15px] leading-7 text-stone-500">选择一个已连接的仓库，描述目标。DevFlow 会规划任务、准备环境、编写代码并验证结果。</p>
      </div>
      <TaskComposer />
      <RecentProjects />
    </section>
  );
}

function RecentProjects() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  if (!projects.data?.length) return <p className="mt-8 text-center text-sm text-stone-400">先到“项目”连接一个 GitHub 仓库，再开始开发。</p>;
  return <section className="mt-9 w-full max-w-6xl"><h2 className="text-sm font-medium text-stone-500">最近项目</h2><div className="mt-3 grid gap-4 sm:grid-cols-2">{projects.data.slice(0, 4).map((project) => <Link key={project.project_id} to={`/runs/new?projectId=${project.project_id}`} className="rounded-xl border border-stone-200 bg-white p-5 transition hover:border-stone-300 hover:shadow-sm"><p className="truncate font-medium text-stone-800">{project.repository_url.replace(/\/$/, "").split("/").slice(-2).join("/")}</p><p className="mt-2 text-xs text-stone-500">GitHub {project.workspace_ready ? "已连接" : "待检查"} · {project.default_branch} · {project.run_count} 次运行</p></Link>)}</div></section>;
}
