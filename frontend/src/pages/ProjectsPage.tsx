import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";

import { createProject, listProjects } from "../api/product";
import { labelFor } from "../i18n";
import type { ProductProject } from "../types/product";

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [githubPublicationToken, setGithubPublicationToken] = useState("");
  const [registeredProject, setRegisteredProject] = useState<ProductProject | null>(null);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: listProjects,
  });
  const create = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      setRepositoryUrl("");
      setGithubPublicationToken("");
      setRegisteredProject(project);
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({
      repository_url: repositoryUrl,
      default_branch: defaultBranch,
      github_publication_token: githubPublicationToken,
    });
  }

  return (
    <section className="space-y-8">
      <div>
        <p className="text-sm font-semibold uppercase tracking-[0.2em] text-cyan-300">
          项目管理
        </p>
        <h1 className="mt-2 text-4xl font-semibold text-white">项目</h1>
        <p className="mt-3 max-w-3xl text-slate-400">
          通过后端管理的工作区注册代码仓库。浏览器可见配置不会包含 Git 或模型服务凭据。
        </p>
      </div>

      <form
        onSubmit={submit}
        className="grid gap-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5 md:grid-cols-[1fr_12rem_1fr_auto]"
      >
        <label className="space-y-2 text-sm text-slate-300">
          <span>HTTPS 仓库地址</span>
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
          <span>GitHub 发布令牌</span>
          <input
            required
            type="password"
            autoComplete="off"
            value={githubPublicationToken}
            onChange={(event) => setGithubPublicationToken(event.target.value)}
            placeholder="Fine-grained PAT"
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2"
          />
        </label>
        <label className="space-y-2 text-sm text-slate-300">
          <span>默认分支</span>
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
          {create.isPending ? "正在注册…" : "注册项目"}
        </button>
        {create.error ? (
          <p className="text-sm text-rose-300 md:col-span-4">
            {registrationErrorMessage(create.error.message)}
          </p>
        ) : null}
        {registeredProject ? (
          <p className="text-sm text-emerald-300 md:col-span-4" role="status">
            项目注册成功：{registeredProject.repository_url}（{registeredProject.default_branch}）已就绪。
          </p>
        ) : null}
        <p className="text-xs text-slate-500 md:col-span-4">
          令牌会使用本机加密密钥加密后保存到 DevFlow PostgreSQL 数据卷；不会返回浏览器或写入日志。保持本机 .env 中的加密密钥不变，重启后仍可发布。
        </p>
      </form>

      <div className="grid gap-4">
        {projects.isLoading ? <p className="text-slate-400">正在加载项目…</p> : null}
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
                  {project.default_branch} · {project.run_count} 次运行
                </p>
                <ProjectProvisionState project={project} />
              </div>
              <div className="flex gap-2">
                <Link
                  to={`/runs?projectId=${project.project_id}`}
                  className="rounded-md border border-slate-700 px-3 py-2 text-sm"
                >
                  查看运行记录
                </Link>
                <Link
                  to={`/runs/new?projectId=${project.project_id}`}
                  className="rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950"
                >
                  新建运行
                </Link>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ProjectProvisionState({ project }: { project: ProductProject }) {
  const stateLabel = labelFor(project.provision_status);
  if (project.provision_status === "READY" && project.workspace_ready) {
    return <p className="mt-2 text-sm text-emerald-300">工作区已就绪</p>;
  }
  if (project.provision_status === "PROVISIONING") {
    return (
      <p className="mt-2 text-sm text-cyan-200" aria-live="polite">
        工作区{stateLabel}。正在克隆仓库并校验默认分支，请勿重复提交。
      </p>
    );
  }
  if (project.provision_status === "FAILED") {
    return (
      <div className="mt-2 text-sm text-rose-300" role="alert">
        <p>该项目上一次注册失败：{projectFailureGuidance(project.provision_error_code)}</p>
        {project.provision_error_code ? (
          <p className="mt-1 font-mono text-xs text-rose-200/70">
            诊断代码：{project.provision_error_code}
          </p>
        ) : null}
      </div>
    );
  }
  return (
    <p className="mt-2 text-sm text-amber-200">
      工作区{stateLabel}，当前不能启动运行。
    </p>
  );
}

function registrationErrorMessage(message: string): string {
  if (message.includes("git project provisioning/sync")) {
    return "仓库注册失败：无法从 GitHub 获取仓库。请确认地址、访问权限、默认分支及网络/代理设置后重试。";
  }
  return message;
}

function projectFailureGuidance(code: string | null): string {
  if (code === "GIT_TIMEOUT") {
    return "连接 GitHub 超时。请检查网络、代理或 VPN 后重新注册。";
  }
  if (code === "GIT_COMMAND_FAILED") {
    return "无法从 GitHub 获取仓库。请检查仓库地址、访问权限、默认分支和网络连接。";
  }
  if (code === "GIT_UNAVAILABLE") {
    return "系统未找到 Git。安装 Git 或修复 PATH 后重新启动 DevFlow。";
  }
  if (code === "SUBMODULE_PROJECT_UNSUPPORTED") {
    return "该仓库包含 Git 子模块，当前版本暂不支持注册。";
  }
  return "工作区初始化未完成。请查看诊断代码并在问题修复后重新注册。";
}
