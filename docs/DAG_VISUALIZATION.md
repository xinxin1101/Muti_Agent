# DAG Visualization

Phase 4 Step 4.4 makes the already validated Task DAG visible in the product without moving DAG or scheduler authority into the browser.

## Authority chain

```text
validated TaskDAG
        ↓
atomic PostgreSQL Run + DAG snapshot
        ↓
canonical DAG SHA-256
        ↓
backend ProductRunDAG projection
        ↓
React read-only SVG
```

Runtime freshness is separate:

```text
accepted typed state evidence
        ↓
ProductRunDAG presentation state
        ↑
SSE only invalidates the DAG query
```

The browser never constructs dependency edges from task order, runtime-event prose, or local UI state. It never writes topology, changes READY/BLOCKED decisions, starts tasks, or bypasses the deterministic scheduler.

## Persisted topology

Migration `0005_task_dag_dependencies` adds:

- nullable `tasks.depends_on` JSONB;
- nullable `runs.dag_sha256`.

NULL is intentional for legacy Runs. It means authoritative topology was not persisted under the Step 4.4 contract.

For new Product Runs, `PostgresDAGStore.start_run()` performs one PostgreSQL transaction containing:

```text
RunRow + dag_sha256
TaskRow + TaskContract hash + depends_on
RUN_STARTED structured event
```

Only after that transaction commits may Dramatiq dispatch occur. A failure while freezing Run/DAG identity therefore rolls back the whole Run instead of leaving a RUNNING, undispatched half-record.

`PostgresDAGStore.start_run()` and `persist_dag()` accept only an already validated `TaskDAG`. Topology is normalized deterministically before hashing. The persisted task identities/contracts must agree with the DAG, and a previously frozen DAG cannot be changed. `persist_dag()` remains a bounded compatibility path for an existing still-running Run that has no accepted topology; same-DAG replay is idempotent and conflicting topology fails closed.

`PostgresDAGStore.load_dag()` reconstructs `TaskDAG`, re-runs its unknown-dependency/self-dependency/cycle validation, and verifies the canonical hash. Corrupted topology therefore cannot become browser data.

A legacy single-task Run is safely representable as one node with zero possible dependency edges and is marked `IMPLICIT_SINGLE_TASK`. A legacy multi-task Run with no persisted topology fails with `PersistenceDAGUnavailableError`; Step 4.4 never rewrites it as independent tasks.

## Product projection

`GET /api/v1/runs/{run_id}/dag` exposes only a typed read model:

- Run id and DAG SHA-256;
- topology source;
- deterministic topological order;
- nodes with objective, dependency ids, topological index, and layer;
- directed dependency edges;
- presentation state and its evidence/derived basis.

No API in Step 4.4 accepts DAG mutations.

Presentation state is built on the server. The latest accepted typed `STATE_TRANSITION` evidence is used for active/terminal task states. For tasks that have not advanced beyond PENDING, the validated DAG derives READY or BLOCKED from completed/failed upstream evidence. Contradictory evidence such as an advanced task downstream of a failed dependency fails closed as persistence corruption. This is a UI projection; the deterministic scheduler remains the execution authority.

## SSE boundary

Step 4.3 SSE does not mutate nodes directly. On accepted evidence/finalization events, React invalidates the `run-dag` TanStack Query and re-reads the typed backend projection. EventSource remains a freshness signal, not a scheduler or DAG-state store.

## Frontend

`RunDAG` renders deterministic SVG positions from backend-provided layers/topological indexes and backend-provided edges. Nodes link to the existing Task Detail route. There are no drag handles, edge editors, dependency toggles, scheduling controls, or browser-authored state transitions.

## Deferred

Step 4.4 does not implement diff viewing, run metrics, GitHub branch/Draft PR publication, benchmark/demo work, or general multi-task Run creation in the product form. Those remain separate roadmap boundaries.

Frozen principle:

> **The browser may render validated DAG truth and refreshed evidence-derived presentation state; it may not create, edit, schedule, or replace that truth.**
