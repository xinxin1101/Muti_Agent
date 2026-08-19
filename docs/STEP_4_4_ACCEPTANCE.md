# Step 4.4 Acceptance — DAG Visualization

Status: **CANDIDATE / PENDING CI**

Step 4.4 is ready for acceptance when the exact PR head proves:

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
- migration upgrade/downgrade, verifier build, Ruff, and the complete backend suite remain green;
- frontend locked install, strict typecheck, lint, tests, and production build remain green;
- no Diff Viewer, Metrics, GitHub publication, benchmark/demo, or product multi-task authoring is introduced.

Frozen candidate principle:

> **The browser may render validated DAG truth and refreshed evidence-derived presentation state; it may not create, edit, schedule, or replace that truth.**
