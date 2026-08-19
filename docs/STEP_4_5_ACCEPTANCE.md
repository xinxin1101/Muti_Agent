# Step 4.5 Acceptance — Diff Viewer

Status: **CANDIDATE / PENDING CI**

Step 4.5 is ready for acceptance when the exact PR head proves:

- `TASK` commit pairs come only from validated successful `WORKER_EXECUTION` evidence;
- `INTEGRATION` commit pairs come only from validated `MERGE_QUEUE_SNAPSHOT` integrated attempts;
- the browser can select only `TASK` or `INTEGRATION` evidence kind and cannot submit base/head commit identities;
- unexpected diff query parameters and duplicate `kind` selectors fail closed;
- task commit identity is revalidated as an existing Git commit with exactly the recorded task base as parent;
- integrated task commit identity is revalidated against its recorded task base;
- integration commit identity is revalidated with the exact ordered parent pair `(previous integration head, task commit)`;
- merge queue Run base must match persisted Run base;
- malformed, conflicting, missing, or irreproducible Git evidence cannot be rendered as trusted code history;
- unavailable Task/Integration evidence returns a bounded unavailable response rather than accepting user-supplied replacement SHAs;
- Git extraction is read-only and does not mutate HEAD, branches, refs, index, worktrees, or integration state;
- output is deterministically bounded by file count, per-file patch bytes, total patch bytes, blob size, and Git timeout;
- binary patch bodies are omitted explicitly;
- bounded patches/files carry explicit truncation/omission metadata;
- React renders the typed diff as text and exposes no edit/stage/commit/reset/merge/ref controls;
- Backend Quality and Frontend Quality remain green on the same exact head;
- no Run Metrics, GitHub publication, benchmark/demo, or new scheduler authority is introduced.

Frozen candidate principle:

> **Persisted typed evidence chooses the commit pair; Git proves the code delta; the browser only renders the bounded result.**
