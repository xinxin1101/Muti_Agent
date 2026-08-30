import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getGitHubPublication, publishGitHubDraft } from "../api/product";
import { labelFor } from "../i18n";
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
        aria-label="GitHub 发布"
      >
        <h2 className="text-xl font-semibold text-white">GitHub 发布</h2>
        <p className="mt-2 text-sm text-slate-400">
          仅当已持久化的运行状态为“已成功”后，才可发布草稿 PR。
        </p>
        <p className="mt-2 text-xs text-slate-500">
          GitHub 状态不会提升或覆盖运行状态。
        </p>
      </section>
    );
  }

  if (publication.isLoading) {
    return (
      <p className="rounded-xl border border-slate-800 bg-slate-950/60 p-5 text-sm text-slate-500">
        正在加载 GitHub 发布资格…
      </p>
    );
  }

  if (publication.error || !publication.data) {
    return (
      <section
        className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-5"
        aria-label="GitHub 发布"
      >
        <h2 className="font-semibold text-amber-100">GitHub 发布不可用</h2>
        <p className="mt-2 text-sm text-amber-200/80">
          {publication.error?.message ??
            "已接受的运行时证据未定义可发布的来源。"}
        </p>
      </section>
    );
  }

  const item = publication.data;
  const publishing = item.state === "PUBLISHING";
  return (
    <section
      className="space-y-4 rounded-xl border border-slate-800 bg-slate-900/50 p-5"
      aria-label="GitHub 发布"
    >
      <div>
        <h2 className="text-xl font-semibold text-white">GitHub 发布</h2>
        <p className="mt-1 text-sm text-slate-500">
          将后端选定的已接受提交发布到 DevFlow 管理的分支与草稿 PR。GitHub 不参与运行成功判定。
        </p>
      </div>

      <dl className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Fact label="状态" value={labelFor(item.state)} />
        <Fact label="来源依据" value={labelFor(item.source_basis)} />
        <Fact label="来源提交" value={item.source_commit.slice(0, 12)} mono />
        <Fact label="证据" value={`#${item.source_evidence_id}`} mono />
        <Fact label="仓库" value={item.repository_slug} />
        <Fact label="基线分支" value={item.base_branch} mono />
        <Fact label="DevFlow 分支" value={item.branch_name} mono />
        <Fact label="尝试次数" value={String(item.attempt_count)} />
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
          打开草稿 PR #{item.pull_request_number}
        </a>
      ) : (
        <button
          type="button"
          disabled={!item.publisher_configured || publish.isPending}
          onClick={() => publish.mutate()}
          className="rounded-lg bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {publish.isPending
            ? "正在发布…"
            : publishing
              ? "重试发布"
              : "创建草稿 PR"}
        </button>
      )}

      {publishing ? (
        <p className="text-xs text-slate-500">
          后端发布声明已存在。重试是安全的：后端会拒绝仍有效的声明，仅在 PostgreSQL 声明过期后接管。
        </p>
      ) : null}
      {!item.publisher_configured && !item.pull_request_url ? (
        <p className="text-xs text-amber-200/70">
          后端未加载 GitHub 发布凭据。请在仓库根目录 .env 配置
          DEVFLOW_GITHUB_PUBLICATION_TOKEN，或重新注册该项目并填写发布令牌。项目令牌会加密保存于本机 PostgreSQL 数据卷；浏览器不会保存任何凭据。
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
