# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions, failure history, and merge-review hardening remain preserved in the corresponding pull requests, acceptance documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 4 — V1.0 Productization**
- Completed item: **Step 4.4 — DAG visualization**
- Next item: **Step 4.5 — Diff viewer**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **ACCEPTED / COMPLETE**
- Phase 4 status: **IN PROGRESS**
- Step 4.1 status: **ACCEPTED / COMPLETE**
- Step 4.2 status: **ACCEPTED / COMPLETE**
- Step 4.3 status: **ACCEPTED / COMPLETE**
- Step 4.4 status: **ACCEPTED / COMPLETE**
- Step 4.5 status: **NOT STARTED**
- V0.1 status: **ACCEPTED / COMPLETE**

---

# Phase 1 — V0.1 Single Task Evidence Loop — ACCEPTED / COMPLETE

Phase 1 was completed through PR #1–#10.

| Step | Capability | Acceptance snapshot |
| --- | --- | --- |
| 1.1 | Project skeleton and quality baseline | Ruff + pytest + GitHub Actions baseline |
| 1.2 | Core Pydantic schemas | 21 tests passed |
| 1.3 | SiliconFlow provider boundary | 31 tests passed; no paid API call in CI |
| 1.4 | Planner structured output gate | 43 tests passed |
| 1.5 | Workspace and Git scope enforcement | 55 tests passed |
| 1.6 | Bounded Developer tool loop | 67 tests passed |
| 1.7 | Deterministic verifier | 76 tests passed |
| 1.8 | Independent semantic Reviewer | 84 tests passed |
| 1.9 | Failure-aware targeted Repair | 97 tests passed |
| 1.10 | Single-task Orchestrator + CLI | 104 tests passed |

Frozen principle:

> **Agents propose; evidence decides.**

---

# Phase 2 — V0.2 True Multi-Agent Runtime — ACCEPTED / COMPLETE

Phase 2 was completed through PR #11–#17.

| Step | Capability | Acceptance snapshot |
| --- | --- | --- |
| 2.1 | Validated Task DAG representation | 120 tests passed |
| 2.2 | Deterministic DAG scheduler | 135 tests passed |
| 2.3 | Isolated Git worktree per task | 153 tests passed |
| 2.4 | Bounded parallel worker execution | 165 tests passed |
| 2.5 | Topological object-level merge queue | 176 tests passed |
| 2.6 | Structured merge-conflict classification | 187 tests passed |
| 2.7 | Evidence-bound Integration / Human Gate | 205 tests passed |

Phase 2 completion commit:

`4d091949f1ba5a57465dc30edd4f5935f1a06fdd`

Frozen Phase 2 boundary:

- validated dependency truth and bounded parallel execution;
- isolated Git worktrees per task;
- successful output frozen as auditable task commits;
- deterministic topological integration;
- structured merge-conflict evidence;
- evidence-bound Integration/Human Gate authorization;
- no LLM self-report can bypass hard evidence gates.

---

# Phase 3 — V0.3 Safety, Context and Reliability — ACCEPTED / COMPLETE

Phase 3 was completed through PR #18–#25.

| Step | Capability | Status | Merge commit | Final acceptance snapshot |
| --- | --- | --- | --- | --- |
| 3.1 | Docker Verification Sandbox + Resource Limits | **ACCEPTED** | `7387e30e545ca05235dd40d06e6ab3fc15fcfbdd` | 220 tests passed |
| 3.2 | Context Packet Builder | **ACCEPTED** | `2a4d948b29045be3cbcf5ffae4f541d9135457bb` | 228 tests passed |
| 3.3 | AST / Import-aware Relevant-code Extraction | **ACCEPTED** | `02c6f1ecfe0398a339c34621443a1596b73a13de` | 240 tests passed |
| 3.4 | PostgreSQL Persistence | **ACCEPTED** | `577da384072cc882c8851497351d99012de1f653` | 248 tests passed |
| 3.5 | Redis + Dramatiq Workers | **ACCEPTED** | `51048e4818478c07e2c024d78215a43959a56465` | 258 tests passed |
| 3.6 | Lease + Heartbeat | **ACCEPTED** | `c7732d3ad832ff4abd159b322c8f39381159ed83` | 270 tests passed |
| 3.7 | `run_token` stale-write protection | **ACCEPTED** | `31a71bc45b58f8c865a54a972e96678e696f5c66` | 273 tests passed |
| 3.8 | Structured run/event logs | **ACCEPTED** | `774ac16bfedcc8d02690ef803fd8f1eee6593158` | 281 tests passed |

Phase 3 completion commit:

`774ac16bfedcc8d02690ef803fd8f1eee6593158`

Detailed accepted design/acceptance documents:

- Step 3.1: sandbox implementation and tests in PR #18;
- Step 3.2: Context Packet implementation and tests in PR #19;
- Step 3.3: `docs/AST_RELEVANCE.md`, `docs/AST_RELEVANCE_ACCEPTANCE.md`;
- Step 3.4: `docs/POSTGRESQL_PERSISTENCE.md` and PR #21;
- Step 3.5: `docs/REDIS_DRAMATIQ_WORKERS.md`, `docs/STEP_3_5_ACCEPTANCE.md`;
- Step 3.6: `docs/LEASE_HEARTBEAT.md`, `docs/STEP_3_6_ACCEPTANCE.md`;
- Step 3.7: `docs/RUN_TOKEN_FENCING.md`, `docs/STEP_3_7_ACCEPTANCE.md`;
- Step 3.8: `docs/STRUCTURED_RUNTIME_EVENTS.md`, `docs/STEP_3_8_ACCEPTANCE.md`.

### Frozen Phase 3 boundary

Phase 3 now guarantees:

1. deterministic project verification executes inside a bounded Docker sandbox;
2. context construction is bounded, provenance-aware, and cannot widen TaskContract scope;
3. AST/import relevance can select useful code regions without becoming a filesystem authority;
4. PostgreSQL durably stores typed/hash-validated runtime evidence;
5. Redis/Dramatiq provides process-external delivery while workers reload truth from PostgreSQL;
6. per-task lease + heartbeat establishes explicit owner/liveness state using PostgreSQL database time;
7. `run_token` generations fence stale worker writes and make EXPIRED-owner takeover safe;
8. runtime-owned Git mutations are fenced against stale generations;
9. accepted Run/Task/Dispatch/Generation/Agent/Verification facts are projected into a compact, append-only structured event timeline;
10. event logging remains observability only and cannot authorize success or bypass deterministic evidence gates.

Frozen Phase 3 principles:

> **Lease establishes liveness; `run_token` establishes write authority.**

> **Typed evidence decides what happened; structured events make that accepted history queryable.**

---

## Step 3.8 — Structured Run/Event Logs — ACCEPTED

Merged through PR #25: `Phase 3 Step 3.8: add structured runtime event timeline`.

Squash merge commit:

`774ac16bfedcc8d02690ef803fd8f1eee6593158`

Design / acceptance documents:

- `docs/STRUCTURED_RUNTIME_EVENTS.md`
- `docs/STEP_3_8_ACCEPTANCE.md`

### Accepted event architecture

```text
accepted typed evidence / lease mutation
        ↓
same PostgreSQL transaction
        ↓
structured runtime event
        ↓
Run-scoped monotonic sequence
        ↓
queryable timeline
```

The timeline is a projection, not a second truth source. Git/worktree still owns code-state truth, typed PostgreSQL evidence owns durable runtime facts, deterministic verification + Reviewer + Git evidence own success decisions, Redis/Dramatiq remains transport, and `run_token` remains a fencing capability.

Persisted event correlation includes:

```text
run_id
task_id
dispatch_id
generation
kind
source
level
sequence
schema_version
bounded attributes + SHA-256
```

`run_token` is intentionally excluded from event fields and generated event payloads.

### Ordering, idempotency, and integrity

- `runs.event_sequence` is incremented while the existing Run row lock is held;
- same-Run accepted facts therefore receive a unique monotonic observable order;
- `(run_id, event_key)` is the event idempotency boundary;
- conflicting event-key reuse fails closed;
- attributes are capped at 16 KiB of canonical JSON;
- structured sensitive attribute keys are rejected before persistence;
- event attributes are SHA-256 validated when read;
- corrupted event rows fail closed as persistence corruption;
- this ordering is an audit/observability primitive and does **not** claim exactly-once execution.

### Transactional projection

Newly accepted typed evidence is projected as `EVIDENCE_RECORDED` in the same transaction. The event stores compact metadata and the typed-evidence hash instead of duplicating raw prompts, model outputs, verification stdout/stderr, or source context.

Structured `source` classification distinguishes Developer, Verification, Reviewer, Repair, Dispatch, Worker, Runtime, and Integration facts.

Run lifecycle records `RUN_STARTED` and `RUN_FINALIZED`. Lease lifecycle records `LEASE_ACQUIRED`, `LEASE_HEARTBEAT`, `LEASE_TAKEN_OVER`, and `LEASE_RELEASED` with task/dispatch/generation correlation.

The Step 3.7 terminal-unwind boundary is preserved: after `RUN_FINALIZED`, typed evidence remains append-closed, while the exact current live owner may still persist the outer `LEASE_RELEASED` cleanup event.

### Query boundary

`PostgresEvidenceStore.list_runtime_events()` supports bounded timeline reads by:

- task;
- dispatch;
- event kind;
- event source;
- `after_sequence` cursor;
- bounded limit.

This makes the persistence layer ready for later API/SSE consumption without implementing frontend streaming in Step 3.8.

Migration:

`0004_structured_runtime_events`

The migration does not synthesize historical events for evidence written before Step 3.8.

### Final acceptance

Final exact-head CI for PR #25:

- PostgreSQL service: **PASS**;
- Redis service: **PASS**;
- Alembic `0001 → 0002 → 0003 → 0004 → downgrade base → 0001 → 0002 → 0003 → 0004`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- structured sensitive-key and 16 KiB bound regressions: **PASS**;
- evidence-kind → structured-source projection regressions: **PASS**;
- same-Run concurrent monotonic-sequence regression: **PASS**;
- lease generation/takeover/no-token-leakage regression: **PASS**;
- terminal `RUN_FINALIZED → LEASE_RELEASED` cleanup regression with typed evidence append-closed: **PASS**;
- event attribute corruption detection: **PASS**;
- `pytest`: **281 passed in 31.34s**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- no paid SiliconFlow API call was required by Step 3.8 acceptance tests.

Frozen Step 3.8 principle:

> **Typed evidence decides what happened; a monotonic structured event projection makes the accepted Run / Task / Dispatch / Generation history queryable without becoming a second success authority.**

---

# Phase 4 — V1.0 Productization — IN PROGRESS

Phase 4 turns the accepted runtime into an operable, observable, and evaluable product without moving runtime authority into the browser.

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 4.1 | React / TypeScript UI Foundation | **ACCEPTED** | locked install + typecheck + lint + 3 UI tests + production build |
| 4.2 | Project / New Run / Dashboard / Task Detail pages | **ACCEPTED** | Backend Quality: 293 passed; Frontend Quality: typecheck + lint + tests + build |
| 4.3 | SSE live status/log updates | **ACCEPTED** | PostgreSQL sequence → SSE/Last-Event-ID + browser fail-closed validation; Backend Quality: 299 passed; Frontend Quality: typecheck + lint + tests + build |
| 4.4 | DAG visualization | **ACCEPTED** | atomic/hash-validated DAG persistence + read-only typed SVG projection; Backend Quality: 307 passed; Frontend Quality: typecheck + lint + tests + build |
| 4.5 | Diff viewer | **NEXT / NOT STARTED** | — |
| 4.6 | Run metrics | **NOT STARTED** | — |
| 4.7 | GitHub branch + Draft PR integration | **NOT STARTED** | — |
| 4.8 | Benchmark/demo suite | **NOT STARTED** | — |

## Step 4.1 — React / TypeScript UI Foundation — ACCEPTED

Merged through PR #26 candidate: `Phase 4 Step 4.1: add React TypeScript UI foundation`.

Design / acceptance documents:

- `docs/FRONTEND_FOUNDATION.md`
- `docs/STEP_4_1_ACCEPTANCE.md`

### Accepted frontend foundation

```text
browser
   ↓
React / TypeScript / Vite
   ├── React Router       presentation routes
   ├── TanStack Query     server-state client boundary
   ├── typed API client   HTTP boundary
   ├── Tailwind CSS       presentation layer
   └── Vitest / Testing Library

backend runtime truth
   ↑
never replaced by browser state
```

The browser is explicitly non-authoritative. Scheduling, leases, `run_token` fencing, verification, Reviewer decisions, integration state, typed evidence, and run success remain backend-owned.

Browser-visible configuration is restricted to public values such as `VITE_API_BASE_URL`. Provider credentials, GitHub credentials, database credentials, Redis credentials, and `run_token` remain outside the frontend boundary.

Runtime-event DTOs mirror the accepted backend wire fields and enum values while excluding credentials. This prepares later API/SSE consumption without implementing streaming in Step 4.1.

### Reproducible quality gate

`frontend/package-lock.json` is committed. The final workflow runs under Node.js 24 with read-only repository permission and executes:

```text
npm ci
    ↓
TypeScript strict typecheck
    ↓
Oxlint
    ↓
Vitest + Testing Library
    ↓
Vite production build
```

The final Step 4.1 code head passed all five gates. The acceptance and progress documents are also workflow-triggering paths so ledger-only changes cannot bypass exact-head frontend validation.

Frozen Step 4.1 principle:

> **The browser may present runtime truth; it may not manufacture runtime truth.**

---

## Step 4.2 — Project / New Run / Dashboard / Task Detail Pages — ACCEPTED

Accepted through PR #27 candidate: `Phase 4 Step 4.2: add product pages and browser API`.

Design / acceptance documents:

- `docs/PRODUCT_PAGES.md`
- `docs/STEP_4_2_ACCEPTANCE.md`

### Accepted product flow

```text
browser
   ↓
typed FastAPI product boundary
   ├── Projects
   ├── New Run
   ├── Runs
   ├── Run Dashboard
   └── Task Detail
   ↓
validated TaskContract request
   ↓
backend-managed Git workspace
   ↓
actual Git HEAD → base_commit
   ↓
PostgresEvidenceStore.start_run()
   ↓
DramatiqTaskDispatcher.dispatch()
   ↓
existing lease / heartbeat / run_token fenced worker runtime
```

The browser can request work but cannot provide runtime authority. It never supplies `base_commit`, `run_token`, lease state, verification success, Reviewer approval, integration success, or terminal Run status.

Project repository materialization is backend-owned. Product requests accept only absolute HTTPS repository URLs without embedded credentials. Existing managed workspaces must match the persisted origin and default branch; project-level symlink workspaces fail closed.

The product API exposes bounded DTO projections rather than raw database rows. Task Detail exposes the persisted `TaskContract` plus bounded evidence kind/stage/sequence/hash metadata, not raw prompts, model outputs, verifier stdout/stderr, credentials, or fencing capabilities.

### Run launch boundary

New Run deliberately derives repository truth on the server:

```text
browser TaskContract
        ↓
Pydantic validation
        ↓
managed workspace readiness check
        ↓
Git HEAD
        ↓
exact base_commit
        ↓
persist Run
        ↓
existing Dramatiq dispatch path
```

An unready/untrustworthy workspace fails before Run creation. A Redis broker rejection after persistence is returned as `BROKER_UNAVAILABLE`; the API does not fabricate queued execution or task success and makes no exactly-once dispatch claim.

Persisted Run status is constrained by `PersistedRunStatus`, so unknown/corrupted status strings fail closed at the product DTO boundary.

### Accepted product HTTP surface

```text
GET  /healthz
GET  /api/v1/projects
POST /api/v1/projects
GET  /api/v1/projects/{project_id}
GET  /api/v1/runs
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/tasks/{task_id}
```

SSE/WebSocket streaming is intentionally absent from Step 4.2.

### Final acceptance snapshot

The hardened Step 4.2 code path passed:

- PostgreSQL + Redis service startup: **PASS**;
- Alembic full upgrade → downgrade base → upgrade head: **PASS**;
- verification Docker image build: **PASS**;
- Ruff: **PASS**;
- backend pytest: **293 passed in 33.82s**;
- previous Starlette/TestClient deprecation warning: **removed**;
- Frontend Quality locked install: **PASS**;
- TypeScript strict typecheck: **PASS**;
- frontend lint: **PASS**;
- Vitest / Testing Library product-page and API-client regressions: **PASS**;
- Vite production build: **PASS**;
- Backend and Frontend workflow path gates cover Step 4.2 design/acceptance/progress documents;
- both workflows retain read-only repository permissions.

Frozen Step 4.2 principle:

> **The browser may request work; Git, typed persistence, and the accepted runtime still decide what work exists and what happened.**

---

## Step 4.3 — SSE Live Status / Logs — ACCEPTED

Accepted through PR #28 candidate: `Phase 4 Step 4.3: add SSE live runtime timeline`.

Design / acceptance documents:

- `docs/SSE_LIVE_EVENTS.md`
- `docs/STEP_4_3_ACCEPTANCE.md`

### Accepted live-event flow

```text
accepted typed/runtime fact
        ↓
PostgreSQL runtime_events
        ↓
Run-scoped monotonic sequence
        ↓
PostgresEvidenceStore.list_runtime_events()
        ↓
read-only FastAPI SSE
        ↓
SSE id = sequence
        ↓
native EventSource / Last-Event-ID
        ↓
validated bounded React timeline
```

The stream does not become a scheduler or write path. It cannot acquire/renew leases, create or consume `run_token`, write evidence, advance integration, or declare a Run successful.

No Redis Pub/Sub or worker-memory event bus was introduced. PostgreSQL remains the accepted event-history projection, and the SSE layer performs bounded incremental reads using the already accepted `after_sequence` query boundary.

### Resume, heartbeat, and browser fencing

Reconnect uses the furthest valid `Last-Event-ID` / `after_sequence` value and requests only persisted events with a greater Run sequence. No second cursor system exists.

An unknown Run is preflighted before stream start so it returns HTTP 404 normally. Invalid cursors, cross-Run/non-monotonic server batches, sensitive nested attributes, and oversized SSE payloads fail closed.

Heartbeat is only an SSE comment:

```text
: heartbeat after_sequence=<cursor>
```

It has no event id, consumes no runtime sequence, and creates no persistence evidence.

The browser runtime-validates event shape, enums, hashes, Run identity, and sequence ordering before rendering. An exact duplicate of the latest sequence/event identity is idempotent; backward movement, sequence reuse with a different `event_id`, cross-Run data, or gaps fail closed and close that stream.

EventSource is opened only after the existing REST Run query confirms the Run. SSE does not assign the Run status badge: `EVIDENCE_RECORDED` / `RUN_FINALIZED` only invalidate the REST query so typed persisted Run truth is re-read.

The rendered timeline keeps at most the newest 500 accepted browser events and closes EventSource on unmount.

### Final acceptance snapshot

The hardened Step 4.3 code path passed:

- PostgreSQL + Redis service startup: **PASS**;
- Alembic full upgrade → downgrade base → upgrade head: **PASS**;
- verification Docker image build: **PASS**;
- Ruff: **PASS**;
- backend pytest: **299 passed in 35.63s**;
- SSE resume/order/sensitive-attribute/unknown-Run regressions: **PASS**;
- Frontend Quality locked install: **PASS**;
- TypeScript strict typecheck: **PASS**;
- frontend lint: **PASS**;
- runtime-event parser / EventSource ordering / cleanup regressions: **PASS**;
- Vite production build: **PASS**;
- Backend and Frontend workflow path gates cover Step 4.3 design/acceptance/progress documents;
- both workflows retain read-only repository permissions.

Frozen Step 4.3 principle:

> **SSE may make accepted runtime history live; it may not become a second runtime history or a second success authority.**

---

## Step 4.4 — DAG Visualization — ACCEPTED

Accepted through PR #29 candidate: `Phase 4 Step 4.4: add validated DAG visualization`.

Design / acceptance documents:

- `docs/DAG_VISUALIZATION.md`
- `docs/STEP_4_4_ACCEPTANCE.md`

### Accepted DAG authority chain

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

New Product Run creation freezes `RunRow`, task contract hashes, dependency lists, DAG hash, and the `RUN_STARTED` event in one PostgreSQL transaction before Dramatiq dispatch. A persistence failure therefore cannot leave a newly created Product Run with missing topology while still dispatching work.

`PostgresDAGStore` is the dedicated topology authority. It reconstructs the persisted graph through the existing validated `TaskDAG`, so unknown dependencies, self-dependencies, and cycles remain fail-closed. The normalized topology is checked against a canonical SHA-256 and is immutable once recorded. The generic `PersistedRunSnapshot` deliberately does not expose dependency topology, preventing a second unvalidated DAG read path.

Legacy compatibility also fails closed: an old single-task Run can be represented as `IMPLICIT_SINGLE_TASK`, because there is no possible dependency edge to lose; an old multi-task Run without authoritative persisted topology returns unavailable rather than pretending every task is independent.

### Presentation-state boundary

`GET /api/v1/runs/{run_id}/dag` is read-only. It returns typed nodes, directed edges, deterministic topological order/layers, topology source/hash, and node presentation-state basis.

The backend reads accepted typed `STATE_TRANSITION` evidence for active and terminal states. For tasks that have not advanced, the validated DAG may derive READY/BLOCKED presentation state from accepted completed/failed upstream evidence. Contradictory advanced state downstream of a failed dependency is treated as persistence corruption rather than displayed as a valid graph state.

Step 4.3 SSE remains a freshness signal only. Accepted evidence/finalization events invalidate the `run-dag` TanStack Query, and React re-reads the backend projection; EventSource never directly edits node state.

The deterministic SVG renderer consumes only backend-provided layers and edges. DAG nodes link to the existing Task Detail page. No drag/drop, edge editing, dependency toggles, manual unblock, scheduler control, or browser-authored task transitions are exposed.

### Final acceptance snapshot

The hardened Step 4.4 code head passed:

- PostgreSQL + Redis service startup: **PASS**;
- Alembic `0001 → 0002 → 0003 → 0004 → 0005 → downgrade base → upgrade head`: **PASS**;
- verification Docker image build: **PASS**;
- Ruff: **PASS**;
- atomic Run/DAG persistence and rollback regressions: **PASS**;
- DAG identity/hash/immutability/corruption regressions: **PASS**;
- legacy topology fail-closed regressions: **PASS**;
- backend evidence-derived DAG state regressions: **PASS**;
- backend pytest: **307 passed in 32.04s**;
- Frontend Quality locked install: **PASS**;
- TypeScript strict typecheck: **PASS**;
- frontend lint: **PASS**;
- DAG rendering / Task Detail navigation / SSE refresh regressions: **PASS**;
- Vite production build: **PASS**;
- Backend and Frontend workflow path gates cover Step 4.4 design/acceptance/progress documents;
- both workflows retain read-only repository permissions.

Frozen Step 4.4 principle:

> **The browser may render validated DAG truth and refreshed evidence-derived presentation state; it may not create, edit, schedule, or replace that truth.**

---

## Gate before Step 4.5 — Diff Viewer

The next roadmap item is **Step 4.5 — Diff viewer**.

Step 4.5 may make accepted Git/task-commit changes inspectable in the product, but it must preserve these boundaries:

- Git/worktree/task-commit/integration-ref evidence remains the code-state truth; the browser never becomes a file or Git authority;
- the Diff Viewer is a read-only projection and cannot edit files, stage changes, create commits, rewrite task commits, or advance the integration ref;
- diff source commits/refs must be selected by backend-validated Run/Task/Git evidence rather than browser-provided arbitrary repository objects;
- changed paths and diff hunks must be bounded so a large repository or binary output cannot become an unbounded browser payload;
- path-scope rules remain enforced by the existing TaskContract/workspace boundaries; the viewer cannot widen readable/writable scope;
- missing, stale, or contradictory Git evidence must fail closed instead of fabricating an empty/successful diff;
- a visible diff is evidence for inspection only and cannot replace deterministic verification, Reviewer approval, merge gates, or Run success authority;
- SSE may invalidate/reload diff metadata when accepted Git/runtime facts change, but it must not author or mutate Git state;
- Run metrics remains Step 4.6;
- GitHub branch / Draft PR publication remains Step 4.7;
- benchmark/demo suite remains Step 4.8.

**Step 4.5 status: NOT STARTED.**
