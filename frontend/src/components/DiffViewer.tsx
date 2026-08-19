import type { ProductDiffFile, ProductTaskDiff } from "../types/product";

type DiffViewerProps = {
  diff: ProductTaskDiff;
};

export function DiffViewer({ diff }: DiffViewerProps) {
  return (
    <div className="space-y-4" aria-label="Read-only Git diff">
      <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-xl font-semibold text-white">Git diff</h2>
            <p className="mt-1 text-sm text-slate-400">
              {diff.diff_kind} · {diff.evidence_basis} evidence #{diff.source_evidence_id}
            </p>
          </div>
          <p className="font-mono text-sm text-slate-300">
            <span className="text-emerald-300">+{diff.additions}</span>{" "}
            <span className="text-rose-300">-{diff.deletions}</span>
          </p>
        </div>
        <div className="mt-4 grid gap-2 text-xs text-slate-400 md:grid-cols-2">
          <p className="break-all font-mono">base {diff.base_commit}</p>
          <p className="break-all font-mono">head {diff.head_commit}</p>
          <p>{diff.changed_file_count} changed files</p>
          <p>{diff.patch_bytes} rendered patch bytes</p>
        </div>
        {diff.truncated ? (
          <p className="mt-3 text-sm text-amber-300">
            Bounded view: {diff.omitted_file_count} files omitted and/or one or more patches truncated.
          </p>
        ) : null}
      </div>

      {diff.changed_file_count === 0 ? (
        <p className="text-slate-500">The validated commit pair has no tree diff.</p>
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
            {file.status}
          </span>
          <span className="break-all font-mono text-sm text-slate-200">{file.path}</span>
        </div>
        <span className="font-mono text-xs text-slate-400">
          {file.binary ? (
            "binary"
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
          Patch omitted by the backend bound: {file.patch_omitted_reason ?? "UNKNOWN"}.
        </p>
      )}

      {file.patch_truncated ? (
        <p className="border-t border-slate-800 px-4 py-2 text-xs text-amber-300">
          This patch is intentionally bounded; inspect the commit directly for the complete artifact.
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