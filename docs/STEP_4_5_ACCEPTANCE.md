# Step 4.5 Acceptance — Diff Viewer

Status: **ACCEPTED / COMPLETE**

Step 4.5 is accepted with the following frozen guarantees and boundaries:

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
- every diff invocation disables external diff helpers and textconv execution with `--no-ext-diff` and `--no-textconv`;
- a side-effecting configured textconv driver is regression-tested and is never executed by the Diff Viewer read path;
- output is deterministically bounded by file count, per-file patch bytes, total patch bytes, blob size, and Git timeout;
- binary patch bodies are omitted explicitly;
- bounded patches/files carry explicit truncation/omission metadata;
- React renders the typed diff as text and exposes no edit/stage/commit/reset/merge/ref controls;
- no Run Metrics, GitHub publication, benchmark/demo, or new scheduler authority is introduced.

## Final exact-head acceptance

Accepted code head before ledger advancement:

`6eb3b9437d6b51f1e6bdcbb112f98a22da6af347`

Backend Quality on that exact head:

- PostgreSQL + Redis service startup: **PASS**;
- existing Alembic `0001 → 0002 → 0003 → 0004 → 0005 → downgrade base → 0001 → 0002 → 0003 → 0004 → 0005`: **PASS**;
- no Step 4.5 database migration was introduced;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- successful Worker evidence → Task commit pair regression: **PASS**;
- successful Merge Queue evidence → two-parent Integration commit pair regression: **PASS**;
- wrong Task direct-parent evidence fails closed: **PASS**;
- reversed Integration parent order fails closed: **PASS**;
- arbitrary SHA/query-selector injection regressions: **PASS**;
- external diff/textconv execution fencing regression: **PASS**;
- binary/large/blob/file-count/patch-byte bound regressions: **PASS**;
- complete backend `pytest`: **317 passed in 34.18s**.

Frontend Quality on the same exact head:

- locked `npm ci`: **PASS**;
- strict TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **20 passed across 5 test files**;
- Task Diff rendering without Git mutation controls: **PASS**;
- `TASK → INTEGRATION` evidence-view switching while leaving commit selection backend-owned: **PASS**;
- existing DAG/SSE/product-page regressions: **PASS**;
- Vite production build: **PASS**.

Both `docs/DIFF_VIEWER.md`, this acceptance document, and `docs/PROGRESS.md` are covered by Backend Quality and Frontend Quality path gates. The acceptance/progress ledger update therefore must pass both workflows again before merge.

Frozen Step 4.5 principle:

> **Persisted typed evidence chooses the commit pair; Git proves the code delta; the browser only renders the bounded result.**
