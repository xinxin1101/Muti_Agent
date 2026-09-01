import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";

import {
  archiveProject,
  createProject,
  deleteProject,
  getProjectDeletionPreview,
  listProjects,
  restoreProject,
} from "../api/product";
import { labelFor } from "../i18n";
import type { ProductProject, ProductProjectDeletionPreview } from "../types/product";

export function ProjectsPage() {
  const queryClient = useQueryClient();
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [githubPublicationToken, setGithubPublicationToken] = useState("");
  const [registeredProject, setRegisteredProject] = useState<ProductProject | null>(null);
  const [deletionPreview, setDeletionPreview] = useState<ProductProjectDeletionPreview | null>(null);
  const [confirmationName, setConfirmationName] = useState("");
  const projects = useQuery({
    queryKey: ["projects", "include-archived"],
    queryFn: () => listProjects(true),
  });
  const invalidateProjectLists = async () => {
    await queryClient.invalidateQueries({ queryKey: ["projects"] });
    await queryClient.invalidateQueries({ queryKey: ["runs"] });
  };
  const archive = useMutation({
    mutationFn: archiveProject,
    onSettled: invalidateProjectLists,
  });
  const restore = useMutation({
    mutationFn: restoreProject,
    onSettled: invalidateProjectLists,
  });
  const previewDeletion = useMutation({
    mutationFn: getProjectDeletionPreview,
    onSuccess: (preview) => {
      setDeletionPreview(preview);
      setConfirmationName("");
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteProject(deletionPreview!.project_id, {
      confirmation_token: deletionPreview!.confirmation_token,
      confirmation_name: confirmationName,
    }),
    onSuccess: async () => {
      setDeletionPreview(null);
      await invalidateProjectLists();
    },
  });
  const create = useMutation({
    mutationFn: createProject,
    onSuccess: (project) => {
      setRepositoryUrl("");
      setGithubPublicationToken("");
      setRegisteredProject(project);
    },
    onSettled: async () => {
      await invalidateProjectLists();
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
        <h1 className="mt-2 text-4xl font-semibold text-stone-900">项目</h1>
        <p className="mt-3 max-w-3xl text-stone-600">
          通过后端管理的工作区注册代码仓库。浏览器可见配置不会包含 Git 或模型服务凭据。
        </p>
      </div>

      <form
        onSubmit={submit}
        className="grid gap-4 rounded-2xl border border-stone-200 bg-white p-5 shadow-sm md:grid-cols-[1fr_12rem_1fr_auto]"
      >
        <label className="space-y-2 text-sm text-stone-700">
          <span>HTTPS 仓库地址</span>
          <input
            required
            type="url"
            value={repositoryUrl}
            onChange={(event) => setRepositoryUrl(event.target.value)}
            placeholder="https://github.com/org/repository"
            className="df-input"
          />
        </label>
        <label className="space-y-2 text-sm text-stone-700">
          <span>GitHub 发布令牌</span>
          <input
            required
            type="password"
            autoComplete="off"
            value={githubPublicationToken}
            onChange={(event) => setGithubPublicationToken(event.target.value)}
            placeholder="Fine-grained PAT"
            className="df-input"
          />
        </label>
        <label className="space-y-2 text-sm text-stone-700">
          <span>默认分支</span>
          <input
            required
            value={defaultBranch}
            onChange={(event) => setDefaultBranch(event.target.value)}
            className="df-input"
          />
        </label>
        <button
          type="submit"
          disabled={create.isPending}
          className="df-button df-button-primary self-end"
        >
          {create.isPending ? "正在注册…" : "注册项目"}
        </button>
        {create.error ? (
          <p className="text-sm text-rose-700 md:col-span-4">
            {registrationErrorMessage(create.error.message)}
          </p>
        ) : null}
        {registeredProject ? (
          <p className="text-sm text-emerald-700 md:col-span-4" role="status">
            项目注册成功：{registeredProject.repository_url}（{registeredProject.default_branch}）已就绪。
          </p>
        ) : null}
        <p className="text-xs text-stone-500 md:col-span-4">
          令牌会使用本机加密密钥加密后保存到 DevFlow PostgreSQL 数据卷；不会返回浏览器或写入日志。保持本机 .env 中的加密密钥不变，重启后仍可发布。
        </p>
      </form>

      <div className="grid gap-4">
        {projects.isLoading ? <p className="text-stone-500">正在加载项目…</p> : null}
        {projects.error ? (
          <p className="text-rose-700">{projects.error.message}</p>
        ) : null}
        {projects.data?.map((project) => (
          <article
            key={project.project_id}
            className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm"
          >
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <h2 className="font-semibold text-stone-900">
                  {project.repository_url}
                </h2>
                <p className="mt-1 text-sm text-stone-500">
                  {project.default_branch} · {project.run_count} 次运行 · {project.lifecycle_state === "ARCHIVED" ? "已归档" : "正常"}
                </p>
                <ProjectProvisionState project={project} />
              </div>
              <div className="flex flex-wrap gap-2">
                {project.lifecycle_state !== "ARCHIVED" ? <>
                  <Link
                    to={`/runs?projectId=${project.project_id}`}
                    className="df-button df-button-secondary"
                  >
                    查看运行记录
                  </Link>
                  <Link
                    to={`/runs/new?projectId=${project.project_id}`}
                    className="df-button df-button-primary"
                  >
                    新建运行
                  </Link>
                </> : null}
                {project.lifecycle_state !== "ARCHIVED" ? <button
                  type="button"
                  onClick={() => archive.mutate(project.project_id)}
                  disabled={archive.isPending}
                  className="df-button df-button-secondary"
                >归档</button> : <button
                  type="button"
                  onClick={() => restore.mutate(project.project_id)}
                  disabled={restore.isPending}
                  className="df-button border border-emerald-600 bg-emerald-50 text-emerald-800"
                >恢复</button>}
                <button
                  type="button"
                  onClick={() => previewDeletion.mutate(project.project_id)}
                  disabled={previewDeletion.isPending}
                  className="df-button border border-rose-300 bg-rose-50 text-rose-800"
                >永久删除</button>
              </div>
            </div>
          </article>
        ))}
      </div>
      {deletionPreview ? <section role="dialog" aria-modal="true" aria-label="永久删除项目确认" className="df-dialog-backdrop fixed inset-0 z-50 grid place-items-center p-4">
        <div className="df-dialog w-full max-w-xl border-rose-200 p-6">
          <h2 className="text-xl font-semibold text-stone-900">永久删除本地项目数据？</h2>
          <p className="mt-3 text-sm leading-6 text-stone-600">这会删除 DevFlow 本地数据库记录、项目工作区、项目专属缓存与本地凭据；不会删除 GitHub 仓库。</p>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm text-stone-700">
            <div><dt className="text-stone-500">运行记录</dt><dd>{deletionPreview.run_count}</dd></div>
            <div><dt className="text-stone-500">开发会话</dt><dd>{deletionPreview.development_session_count}</dd></div>
            <div><dt className="text-stone-500">本地工作区</dt><dd>{formatBytes(deletionPreview.local_workspace_bytes)}</dd></div>
            <div><dt className="text-stone-500">项目专属缓存</dt><dd>{formatBytes(deletionPreview.project_cache_bytes)}</dd></div>
            <div><dt className="text-stone-500">本地凭据</dt><dd>{deletionPreview.local_credential_count}</dd></div>
            <div><dt className="text-stone-500">确认有效期</dt><dd>{new Date(deletionPreview.confirmation_expires_at).toLocaleTimeString()}</dd></div>
          </dl>
          <label className="mt-5 block text-sm text-stone-700">请输入 <strong>{deletionPreview.required_confirmation_name}</strong> 以确认
            <input value={confirmationName} onChange={(event) => setConfirmationName(event.target.value)} className="df-input mt-2" autoComplete="off" />
          </label>
          {remove.error ? <p className="mt-3 text-sm text-rose-700">{remove.error.message}</p> : null}
          <div className="mt-5 flex justify-end gap-3"><button type="button" onClick={() => setDeletionPreview(null)} className="df-button df-button-secondary">取消</button><button type="button" onClick={() => remove.mutate()} disabled={remove.isPending || confirmationName !== deletionPreview.required_confirmation_name} className="df-button df-button-danger">{remove.isPending ? "正在删除…" : "确认永久删除"}</button></div>
        </div>
      </section> : null}
    </section>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
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
