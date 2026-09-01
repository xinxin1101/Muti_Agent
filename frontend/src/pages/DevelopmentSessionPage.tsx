import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import {
  archiveProject,
  archiveRun,
  continueDevelopmentSession,
  deleteProject,
  getDependencyEnvironment,
  getDevelopmentSession,
  getDevelopmentSessionRecovery,
  getDevelopmentSessionTimeline,
  getProject,
  getProjectDeletionPreview,
  previewDevelopmentSessionCommand,
  replanDevelopmentSession,
} from "../api/product";
import { formatDateTime } from "../i18n";
import type {
  ProductDevelopmentSessionCommandPreview,
  ProductProjectDeletionPreview,
} from "../types/product";

export function DevelopmentSessionPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [command, setCommand] = useState("");
  const [confirmation, setConfirmation] = useState<ProductDevelopmentSessionCommandPreview | null>(null);
  const [deletionPreview, setDeletionPreview] = useState<ProductProjectDeletionPreview | null>(null);
  const [confirmationName, setConfirmationName] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);

  const session = useQuery({
    queryKey: ["development-session", sessionId],
    queryFn: () => getDevelopmentSession(sessionId),
    enabled: Boolean(sessionId),
  });
  const project = useQuery({
    queryKey: ["project", session.data?.project_id],
    queryFn: () => getProject(session.data!.project_id),
    enabled: Boolean(session.data?.project_id),
  });
  const timeline = useQuery({
    queryKey: ["development-session-timeline", sessionId],
    queryFn: () => getDevelopmentSessionTimeline(sessionId),
    enabled: Boolean(sessionId),
  });
  const recovery = useQuery({
    queryKey: ["development-session-recovery", sessionId],
    queryFn: () => getDevelopmentSessionRecovery(sessionId),
    enabled: Boolean(sessionId),
  });
  const environment = useQuery({
    queryKey: ["dependency-environment", session.data?.project_id],
    queryFn: () => getDependencyEnvironment(session.data!.project_id),
    enabled: Boolean(session.data?.project_id),
  });

  const commandPreview = useMutation({
    mutationFn: (value: string) => previewDevelopmentSessionCommand(sessionId, value),
    onSuccess: (value) => {
      setConfirmation(value);
      setDeletionPreview(null);
      setConfirmationName("");
      setActionError(null);
      setCommand("");
    },
  });

  if (session.isLoading) return <p className="text-sm text-stone-500">正在加载开发会话…</p>;
  if (session.isError || !session.data || !project.data) {
    return <p className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">无法读取开发会话。请确认它尚未被删除或归档。</p>;
  }

  const executeConfirmedAction = async () => {
    if (!confirmation || !confirmation.executable_after_confirmation) return;
    setActionError(null);
    try {
      if (confirmation.intent === "CONTINUE_DEVELOPMENT") {
        const launch = await continueDevelopmentSession(sessionId, "AUTO");
        await navigate(`/runs/${launch.run_id}`);
        return;
      }
      if (confirmation.intent === "CONTINUE_OLD_BASE") {
        const launch = await continueDevelopmentSession(sessionId, "OLD_BASE");
        await navigate(`/runs/${launch.run_id}`);
        return;
      }
      if (confirmation.intent === "REPLAN") {
        const launch = await replanDevelopmentSession(sessionId);
        await navigate(`/runs/${launch.run_id}`);
        return;
      }
      if (confirmation.intent === "ARCHIVE_RUN" && session.data.latest_run_id) {
        await archiveRun(session.data.latest_run_id);
        await queryClient.invalidateQueries({ queryKey: ["runs"] });
        await queryClient.invalidateQueries({ queryKey: ["development-session-timeline", sessionId] });
        setConfirmation(null);
        return;
      }
      if (confirmation.intent === "ARCHIVE_PROJECT") {
        await archiveProject(session.data.project_id);
        await queryClient.invalidateQueries({ queryKey: ["projects"] });
        await navigate("/");
        return;
      }
      if (confirmation.intent === "DELETE_PROJECT") {
        setDeletionPreview(await getProjectDeletionPreview(session.data.project_id));
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "操作未完成");
    }
  };

  const executeDeletion = async (event: FormEvent) => {
    event.preventDefault();
    if (!deletionPreview) return;
    setActionError(null);
    try {
      await deleteProject(session.data.project_id, {
        confirmation_token: deletionPreview.confirmation_token,
        confirmation_name: confirmationName,
      });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      await navigate("/");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "项目未删除");
    }
  };

  const submitCommand = (event: FormEvent) => {
    event.preventDefault();
    const value = command.trim();
    if (value) commandPreview.mutate(value);
  };

  return (
    <div className="mx-auto grid max-w-7xl gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
      <section className="min-w-0">
        <header className="rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
          <p className="text-sm text-stone-500">开发会话 · {repositoryName(project.data.repository_url)}</p>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-stone-900">{redactSensitiveText(session.data.requirement)}</h1>
          <div className="mt-4 flex flex-wrap gap-2 text-xs text-stone-500">
            <span className="rounded-full bg-stone-100 px-2.5 py-1">{session.data.state}</span>
            <span className="rounded-full bg-stone-100 px-2.5 py-1">{session.data.work_packages.length} 个工作包</span>
            <span className="rounded-full bg-stone-100 px-2.5 py-1">基线 {session.data.base_commit.slice(0, 12)}</span>
          </div>
        </header>

        <section className="mt-5 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm">
          <div className="flex items-center justify-between gap-3"><h2 className="font-semibold text-stone-900">开发过程</h2><span className="text-xs text-stone-400">仅展示受限、持久化事实</span></div>
          {timeline.data?.length ? (
            <ol className="mt-5 space-y-4 border-l border-stone-200 pl-5">
              {timeline.data.map((entry) => (
                <li key={entry.entry_id} className="relative">
                  <span aria-hidden="true" className={`absolute -left-[25px] top-1.5 h-2.5 w-2.5 rounded-full ${timelineDot(entry.kind)}`} />
                  <div className="flex flex-wrap items-baseline justify-between gap-2"><p className="text-sm font-medium text-stone-800">{entry.title}</p><time className="text-xs text-stone-400">{formatDateTime(entry.created_at)}</time></div>
                  {entry.detail ? <p className="mt-1 text-sm leading-6 text-stone-600">{entry.detail}</p> : null}
                  {entry.task_id ? <p className="mt-1 text-xs text-stone-400">工作包：{entry.task_id}</p> : null}
                  {entry.run_id ? <Link to={`/runs/${entry.run_id}`} className="mt-2 inline-block text-xs text-blue-600 hover:underline">查看关联运行</Link> : null}
                </li>
              ))}
            </ol>
          ) : <p className="mt-5 text-sm text-stone-500">会话尚无时间线条目。</p>}
        </section>

        <section className="mt-5 rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <form onSubmit={submitCommand}>
            <label htmlFor="session-command" className="text-sm font-medium text-stone-800">告诉 DevFlow 下一步要做什么</label>
            <textarea id="session-command" value={command} onChange={(event) => setCommand(event.target.value)} rows={3} placeholder="例如：继续刚才的开发、重新规划、归档当前运行" className="df-input mt-3 resize-none" />
            <div className="mt-3 flex items-center justify-between gap-3"><p className="text-xs text-stone-500">仅识别受限操作；不会将命令发送给模型或保存凭据。</p><button type="submit" disabled={commandPreview.isPending || !command.trim()} className="df-button df-button-primary">{commandPreview.isPending ? "正在解析…" : "生成确认卡片"}</button></div>
          </form>
          {commandPreview.error instanceof Error ? <p className="mt-3 text-sm text-rose-600">解析失败：{commandPreview.error.message}</p> : null}
          {confirmation ? <ConfirmationCard preview={confirmation} deletionPreview={deletionPreview} confirmationName={confirmationName} actionError={actionError} onCancel={() => { setConfirmation(null); setDeletionPreview(null); setActionError(null); }} onConfirm={() => void executeConfirmedAction()} onConfirmationNameChange={setConfirmationName} onDelete={(event) => void executeDeletion(event)} /> : null}
        </section>
      </section>

      <aside className="space-y-4 xl:sticky xl:top-6 xl:h-fit">
        <ContextCard title="恢复计划">
          {recovery.data ? <><p>已复用 {recovery.data.reusable_work_package_ids.length} 个工作包</p><p className="mt-2">未完成 {recovery.data.remaining_work_package_ids.length} 个工作包</p><p className="mt-2 text-stone-500">预计新增 {recovery.data.budget.estimated_new_development_tokens.toLocaleString()} Token</p><p className="mt-2 text-stone-500">已节省约 {recovery.data.budget.estimated_tokens_saved.toLocaleString()} Token</p></> : <p className="text-stone-500">{recovery.isLoading ? "正在读取…" : "恢复预览暂不可用"}</p>}
        </ContextCard>
        <ContextCard title="工作包">
          <ul className="space-y-2">{session.data.work_packages.map((item) => <li key={item.task_id} className="flex items-center justify-between gap-2"><span className="truncate">{item.task_id}</span><span className="text-xs text-stone-500">{item.state}</span></li>)}</ul>
        </ContextCard>
        <ContextCard title="环境状态">
          {environment.data ? <><p>{environment.data.package_manager} · {environment.data.cache_state}</p><p className="mt-2 text-stone-500">{environment.data.profile_kind}</p></> : <p className="text-stone-500">{environment.isLoading ? "正在检查…" : "暂不可用"}</p>}
        </ContextCard>
      </aside>
    </div>
  );
}

function ConfirmationCard({ preview, deletionPreview, confirmationName, actionError, onCancel, onConfirm, onConfirmationNameChange, onDelete }: Readonly<{ preview: ProductDevelopmentSessionCommandPreview; deletionPreview: ProductProjectDeletionPreview | null; confirmationName: string; actionError: string | null; onCancel: () => void; onConfirm: () => void; onConfirmationNameChange: (value: string) => void; onDelete: (event: FormEvent) => void }>) {
  return <div className={`mt-4 rounded-xl border p-4 ${preview.affects_local_data ? "border-rose-200 bg-rose-50" : "border-blue-200 bg-blue-50"}`}><p className="text-sm font-semibold text-stone-900">确认：{preview.action_name}</p><p className="mt-1 text-sm text-stone-600">目标：{preview.target_label}</p><ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-stone-600">{preview.impact.map((item) => <li key={item}>{item}</li>)}</ul><p className="mt-3 text-xs text-stone-500">Token：{preview.token_cost}。{preview.confirmation_hint}</p>{deletionPreview ? <form onSubmit={onDelete} className="mt-4 border-t border-rose-200 pt-4"><label className="text-sm text-stone-700">请输入 <strong>{deletionPreview.required_confirmation_name}</strong> 以永久删除本地数据</label><input value={confirmationName} onChange={(event) => onConfirmationNameChange(event.target.value)} className="df-input mt-2 border-rose-200" /><p className="mt-2 text-xs text-stone-500">将删除 {deletionPreview.run_count} 条运行、{deletionPreview.development_session_count} 个会话和项目专属本地缓存；GitHub 保留。</p><div className="mt-3 flex gap-2"><button type="submit" disabled={confirmationName !== deletionPreview.required_confirmation_name} className="df-button df-button-danger">永久删除本地数据</button><button type="button" onClick={onCancel} className="df-button df-button-secondary">取消</button></div></form> : <div className="mt-4 flex gap-2"><button type="button" onClick={onConfirm} disabled={!preview.executable_after_confirmation} className="df-button df-button-primary">确认操作</button><button type="button" onClick={onCancel} className="df-button df-button-secondary">取消</button></div>}{actionError ? <p className="mt-3 text-sm text-rose-700">操作失败：{actionError}</p> : null}</div>;
}

function ContextCard({ title, children }: Readonly<{ title: string; children: React.ReactNode }>) {
  return <section className="rounded-2xl border border-stone-200 bg-white p-5 text-sm shadow-sm"><h2 className="font-semibold text-stone-800">{title}</h2><div className="mt-3 text-stone-700">{children}</div></section>;
}

function timelineDot(kind: string): string {
  if (kind.includes("FAILED") || kind === "BUDGET_DIAGNOSTIC") return "bg-rose-500";
  if (kind.includes("SUCCEEDED")) return "bg-emerald-500";
  if (kind.includes("CHECKPOINT") || kind.includes("RECOVERY")) return "bg-amber-500";
  return "bg-blue-500";
}

function repositoryName(value: string): string { return value.replace(/\/$/, "").split("/").slice(-2).join("/") || value; }

function redactSensitiveText(value: string): string {
  return value.replace(
    /\b(?:sk|ghp|github_pat|bearer)[_-]?[a-z0-9][a-z0-9_.-]{7,}\b|\b(?:api[_-]?key|token|authorization)\s*[=:]\s*\S+/gi,
    "[已隐藏凭据]",
  );
}
