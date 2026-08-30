import { useQuery } from "@tanstack/react-query";
import { NavLink, Link } from "react-router";

import { listProjects, listRuns } from "../api/product";
import { formatDateTime, labelFor } from "../i18n";

const navigation = [
  { to: "/", label: "工作台", end: true },
  { to: "/projects", label: "项目" },
  { to: "/runs", label: "运行记录", end: true },
] as const;

export function WorkspaceSidebar() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: listProjects });
  const runs = useQuery({ queryKey: ["runs", "all"], queryFn: () => listRuns() });

  return (
    <aside className="workspace-sidebar flex shrink-0 flex-col border-r border-stone-200 bg-stone-50/90 px-3 py-5">
      <div className="px-3">
        <Link to="/" className="inline-flex items-center gap-2 text-base font-semibold tracking-tight text-stone-900">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-stone-900 text-xs text-white">D</span>
          DevFlow
        </Link>
        <p className="mt-2 text-xs leading-5 text-stone-500">面向代码仓库的开发工作区</p>
      </div>

      <Link to="/runs/new" className="mt-5 rounded-xl bg-stone-900 px-3 py-2.5 text-center text-sm font-medium text-white transition hover:bg-stone-700">
        ＋ 新建任务
      </Link>

      <nav aria-label="主导航" className="mt-5 grid gap-1">
        {navigation.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={"end" in item && item.end}
            className={({ isActive }) => [
              "rounded-lg px-3 py-2 text-sm transition",
              isActive ? "bg-stone-200 font-medium text-stone-900" : "text-stone-600 hover:bg-stone-100 hover:text-stone-900",
            ].join(" ")}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <SidebarSection title="项目">
        {projects.data?.length ? projects.data.slice(0, 6).map((project) => (
          <Link key={project.project_id} to={`/runs?projectId=${project.project_id}`} className="block truncate rounded-lg px-2 py-1.5 text-sm text-stone-600 hover:bg-stone-100 hover:text-stone-900" title={project.repository_url}>
            {repositoryName(project.repository_url)}
          </Link>
        )) : <SidebarEmpty loading={projects.isLoading} text="还没有已连接的项目" />}
      </SidebarSection>

      <SidebarSection title="最近运行" className="mt-5">
        {runs.data?.length ? runs.data.slice(0, 7).map((run) => (
          <Link key={run.run_id} to={`/runs/${run.run_id}`} className="block rounded-lg px-2 py-1.5 hover:bg-stone-100">
            <span className="flex items-center gap-2">
              <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${run.status === "SUCCEEDED" ? "bg-emerald-500" : run.status === "FAILED" ? "bg-rose-500" : "bg-amber-500"}`} />
              <span className="truncate text-sm text-stone-600">运行 {run.run_id.slice(0, 8)}</span>
            </span>
            <span className="mt-0.5 block truncate pl-3.5 text-[11px] text-stone-400">{labelFor(run.status)} · {formatDateTime(run.started_at)}</span>
          </Link>
        )) : <SidebarEmpty loading={runs.isLoading} text="暂无运行记录" />}
      </SidebarSection>

      <div className="mt-auto border-t border-stone-200 px-2 pt-4 text-xs text-stone-500">
        环境与设置
      </div>
    </aside>
  );
}

function SidebarSection({ title, className = "mt-7", children }: { title: string; className?: string; children: React.ReactNode }) {
  return <section className={className}><h2 className="px-2 text-xs font-medium text-stone-400">{title}</h2><div className="mt-2 grid gap-0.5">{children}</div></section>;
}

function SidebarEmpty({ loading, text }: { loading: boolean; text: string }) {
  return <p className="px-2 text-xs leading-5 text-stone-400">{loading ? "正在加载…" : text}</p>;
}

function repositoryName(repositoryUrl: string): string {
  return repositoryUrl.replace(/\/$/, "").split("/").slice(-2).join("/") || repositoryUrl;
}
