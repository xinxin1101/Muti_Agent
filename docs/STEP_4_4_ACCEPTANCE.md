# Step 4.4 Acceptance — DAG Visualization

Status: **ACCEPTED / COMPLETE**

Step 4.4 is accepted with the following frozen guarantees and boundaries:

- only a validated `TaskDAG` can be frozen as authoritative Run topology;
- new Product Run identity, task contracts, dependency edges, DAG hash, and `RUN_STARTED` event commit atomically before dispatch;
- a failed atomic DAG Run start leaves no persisted Run identity and cannot dispatch work;
- persisted DAG task ids and TaskContract hashes exactly match the Run tasks;
- canonical DAG SHA-256 detects topology corruption;
- unknown dependency, self-dependency, and cycle validation remains owned by `TaskDAG`;
- a recorded Run DAG is immutable and same-DAG replay is idempotent;
- historical multi-task Runs without authoritative topology fail closed instead of being rendered as independent tasks;
- historical single-task Runs may be projected explicitly as `IMPLICIT_SINGLE_TASK`;
- `GET /api/v1/runs/{run_id}/dag` is read-only and exposes typed nodes/edges/order/layers/state basis;
- node presentation state is built from accepted typed state evidence plus validated-DAG READY/BLOCKED derivation;
- contradictory advanced state downstream of a failed dependency fails closed as persistence corruption;
- SSE only invalidates the DAG query and never writes node state directly;
- React renders backend-provided topology and offers Task Detail navigation without edge/task scheduling controls;
- frontend does not infer dependency edges from task ordering or event prose;
- the generic `PersistedRunSnapshot` deliberately does not expose dependency topology, so `PostgresDAGStore` remains the only hash-validated DAG read path;
- no Diff Viewer, Metrics, GitHub publication, benchmark/demo, or product multi-task authoring is introduced.

## Final exact-head acceptance

Accepted code head before ledger advancement:

`67e4cd9eb67a8c456d983f336802c3be711050c7`

Backend Quality on that exact head:

- PostgreSQL + Redis service startup: **PASS**;
- Alembic `0001 → 0002 → 0003 → 0004 → 0005 → downgrade base → 0001 → 0002 → 0003 → 0004 → 0005`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- atomic Run + DAG + `RUN_STARTED` persistence regressions: **PASS**;
- DAG identity / TaskContract hash / immutable topology regressions: **PASS**;
- DAG SHA-256 corruption detection: **PASS**;
- legacy single-task / multi-task topology boundary regressions: **PASS**;
- evidence-backed READY/BLOCKED presentation-state regressions: **PASS**;
- contradictory downstream state corruption regression: **PASS**;
- complete backend `pytest`: **307 passed in 32.04s**.

Frontend Quality on the same exact head:

- locked `npm ci`: **PASS**;
- strict TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Run Dashboard DAG / Task Detail navigation regressions: **PASS**;
- SSE → DAG query refresh and sequence fail-closed regressions: **PASS**;
- Vite production build: **PASS**.

Both Step 4.4 design/acceptance documents and `docs/PROGRESS.md` are covered by Backend Quality and Frontend Quality path gates with read-only repository permissions. The acceptance/progress ledger update therefore must pass both workflows again before merge.

Frozen Step 4.4 principle:

> **The browser may render validated DAG truth and refreshed evidence-derived presentation state; it may not create, edit, schedule, or replace that truth.**
