import type { ProductDiffFile, ProductTaskDiff } from "../types/product";
import { labelFor } from "../i18n";

type DiffViewerProps = {
  diff: ProductTaskDiff;
};

export function DiffViewer({ diff }: DiffViewerProps) {
  return (
    <div className="space-y-4" aria-label="只读 Git 差异">
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-white">Git 差异</h2>
            <p className="mt-1 text-sm text-slate-400">
              {labelFor(diff.diff_kind)} · {diff.evidence_basis} 证据 #{diff.source_evidence_id}
            </p>
          </div>
          <p className="font-mono text-sm text-slate-300">
            <span className="text-emerald-300">+{diff.additions}</span>{" "}
            <span className="text-rose-300">-{diff.deletions}</span>
          </p>
        </div>
        <div className="mt-4 grid gap-2 text-xs text-slate-400 md:grid-cols-2">
          <p className="break-all font-mono">基线 {diff.base_commit}</p>
          <p className="break-all font-mono">目标 {diff.head_commit}</p>
          <p>{diff.changed_file_count} 个变更文件</p>
          <p>{diff.patch_bytes} 字节已渲染补丁</p>
        </div>
        {diff.truncated ? (
          <p className="mt-3 text-sm text-amber-300">
            受边界约束的视图：已省略 {diff.omitted_file_count} 个文件，和/或一个以上补丁已截断。
          </p>
        ) : null}
      </div>

      {diff.changed_file_count === 0 ? (
        <p className="text-slate-500">已验证的提交对不存在文件树差异。</p>
      ) : null}

      {diff.files.map((file) => (
        <DiffFile key={file.path} file={file} />
      ))}
    </div>
  );
}

function DiffFile({ file }: { file: ProductDiffFile }) {
  return (
    <article className="overflow-hidden rounded-xl border border-slate-800 bg-slate-950/70">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="rounded bg-slate-800 px-2 py-1 font-mono text-xs text-slate-300">
            {labelFor(file.status)}
          </span>
          <span className="break-all font-mono text-sm text-slate-200">{file.path}</span>
        </div>
        <span className="font-mono text-xs text-slate-400">
          {file.binary ? (
            "二进制文件"
          ) : (
            <>
              <span className="text-emerald-300">+{file.additions ?? 0}</span>{" "}
              <span className="text-rose-300">-{file.deletions ?? 0}</span>
            </>
          )}
        </span>
      </div>

      {file.patch !== null ? (
        <pre className="max-h-[36rem] overflow-auto p-4 font-mono text-xs leading-5 text-slate-300">
          {file.patch.split("\n").map((line, index) => (
            <span key={`${index}-${line}`} className={`${lineClass(line)} block whitespace-pre`}>
              {line || " "}
            </span>
          ))}
        </pre>
      ) : (
        <p className="px-4 py-5 text-sm text-slate-500">
          后端因边界限制省略了补丁：{file.patch_omitted_reason ?? "未知原因"}。
        </p>
      )}

      {file.patch_truncated ? (
        <p className="border-t border-slate-800 px-4 py-2 text-xs text-amber-300">
          此补丁被有意限制大小；请直接检查提交以获取完整内容。
        </p>
      ) : null}
    </article>
  );
}

function lineClass(line: string): string {
  if (line.startsWith("@@")) {
    return "text-cyan-300";
  }
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return "text-emerald-300";
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return "text-rose-300";
  }
  if (line.startsWith("diff ") || line.startsWith("index ")) {
    return "text-slate-500";
  }
  return "";
}
