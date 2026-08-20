# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines what should be built; this ledger records what has actually passed acceptance. Detailed design, hardening history, and failure evidence remain in the corresponding acceptance documents, pull requests, CI runs, and Git history.

## Current position

- Current phase: **Phase 5 — V1.1 Durable Agent Runtime — IN PROGRESS**
- Completed item: **Step 5.4 — DAG-wide Run Reconciliation — ACCEPTED / COMPLETE**
- Next item: **Step 5.5 — Durable Human Pause / Resume**
- Phase 1 status: **ACCEPTED / COMPLETE**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **ACCEPTED / COMPLETE**
- Phase 4 status: **ACCEPTED / COMPLETE**
- Phase 5 status: **IN PROGRESS**
- V0.1 status: **ACCEPTED / COMPLETE**
- V0.2 status: **ACCEPTED / COMPLETE**
- V0.3 status: **ACCEPTED / COMPLETE**
- V1.0 status: **ACCEPTED / COMPLETE**
- V1.1 status: **IN PROGRESS**

Frozen project principle:

> **Agents propose; evidence decides.**

Frozen V1.1 principle:

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

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

Phase 1 established the single-task evidence loop:

```text
TaskContract
    ↓
Developer
    ↓
Git scope gate
    ↓
Deterministic verification
    ↓
Independent Reviewer
    ↓
Targeted Repair / terminal result
```

An Agent message never directly sets success.

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

- validated dependency truth determines legal scheduling order;
- parallel tasks execute in isolated Git worktrees;
- accepted task output becomes auditable task commits;
- integration follows deterministic topological order;
- merge conflict location/type/stages are structured as Git evidence;
- conflict classification does not silently repair conflicts;
- Integration/Human Gate authorization remains evidence-bound.

---

# Phase 3 — V0.3 Safety, Context and Reliability — ACCEPTED / COMPLETE

Phase 3 was completed through PR #18–#25.

| Step | Capability | Status | Merge commit | Final snapshot |
| --- | --- | --- | --- | --- |
| 3.1 | Docker Verification Sandbox + Resource Limits | **ACCEPTED** | `7387e30e545ca05235dd40d06e6ab3fc15fcfbdd` | 220 tests |
| 3.2 | Context Packet Builder | **ACCEPTED** | `2a4d948b29045be3cbcf5ffae4f541d9135457bb` | 228 tests |
| 3.3 | AST / Import-aware Relevant-code Extraction | **ACCEPTED** | `02c6f1ecfe0398a339c34621443a1596b73a13de` | 240 tests |
| 3.4 | PostgreSQL Persistence | **ACCEPTED** | `577da384072cc882c8851497351d99012de1f653` | 248 tests |
| 3.5 | Redis + Dramatiq Workers | **ACCEPTED** | `51048e4818478c07e2c024d78215a43959a56465` | 258 tests |
| 3.6 | Lease + Heartbeat | **ACCEPTED** | `c7732d3ad832ff4abd159b322c8f39381159ed83` | 270 tests |
| 3.7 | `run_token` stale-write fencing | **ACCEPTED** | `31a71bc45b58f8c865a54a972e96678e696f5c66` | 273 tests |
| 3.8 | Structured runtime events | **ACCEPTED** | `774ac16bfedcc8d02690ef803fd8f1eee6593158` | 281 tests |

Phase 3 completion commit:

`774ac16bfedcc8d02690ef803fd8f1eee6593158`

Accepted Phase 3 guarantees:

```text
bounded Docker verification
        +
bounded provenance-aware context
        +
PostgreSQL typed/hash-validated evidence
        +
Redis/Dramatiq delivery
        +
DB-time lease/heartbeat
        +
run_token generation fencing
        +
monotonic structured runtime-event projection
```

Frozen Phase 3 principles:

> **Lease establishes liveness; `run_token` establishes write authority.**

> **Typed evidence decides what happened; structured events make that accepted history queryable.**

Detailed documents include `POSTGRESQL_PERSISTENCE.md`, `REDIS_DRAMATIQ_WORKERS.md`, `LEASE_HEARTBEAT.md`, `RUN_TOKEN_FENCING.md`, `STRUCTURED_RUNTIME_EVENTS.md`, and their acceptance ledgers.

---

# Phase 4 — V1.0 Productization — ACCEPTED / COMPLETE

Phase 4 turns the accepted runtime into an operable, observable, publishable, and evaluable product without moving runtime authority into the browser or benchmark layer.

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 4.1 | React / TypeScript UI Foundation | **ACCEPTED** | locked install + typecheck + lint + UI tests + production build |
| 4.2 | Project / New Run / Dashboard / Task Detail | **ACCEPTED** | Backend 293 tests; Frontend quality green |
| 4.3 | SSE live status/log updates | **ACCEPTED** | PostgreSQL sequence → SSE/Last-Event-ID; Backend 299 tests; Frontend green |
| 4.4 | DAG visualization | **ACCEPTED** | hash-validated persisted DAG + read-only SVG; Backend 307 tests; Frontend green |
| 4.5 | Diff viewer | **ACCEPTED** | evidence-selected commit pairs + bounded read-only Git diff; Backend 317 tests |
| 4.6 | Run metrics | **ACCEPTED** | descriptive typed evidence/event projection; Backend 322 tests |
| 4.7 | GitHub branch + Draft PR integration | **ACCEPTED** | evidence-bound/fenced publication projection; Backend 332 tests |
| 4.8 | Benchmark / Demo Suite | **ACCEPTED** | versioned fixtures + raw evaluation metrics + 5 deterministic demos; Backend 361 tests; Frontend green |

## Step 4.1 — Frontend foundation

Accepted boundary:

> **The browser may present runtime truth; it may not manufacture runtime truth.**

Design / acceptance: `FRONTEND_FOUNDATION.md`, `STEP_4_1_ACCEPTANCE.md`.

## Step 4.2 — Product pages and Product API

Accepted boundary:

> **The browser may request work; Git, typed persistence, and the accepted runtime still decide what work exists and what happened.**

Design / acceptance: `PRODUCT_PAGES.md`, `STEP_4_2_ACCEPTANCE.md`.

## Step 4.3 — SSE live runtime timeline

Accepted boundary:

> **SSE may make accepted runtime history live; it may not become a second runtime history or a second success authority.**

Design / acceptance: `SSE_LIVE_EVENTS.md`, `STEP_4_3_ACCEPTANCE.md`.

## Step 4.4 — DAG visualization

Accepted boundary:

> **The browser may render validated DAG truth and refreshed evidence-derived presentation state; it may not create, edit, schedule, or replace that truth.**

Design / acceptance: `DAG_VISUALIZATION.md`, `STEP_4_4_ACCEPTANCE.md`.

## Step 4.5 — Diff viewer

Accepted boundary:

> **Persisted typed evidence chooses the commit pair; Git proves the code delta; the browser only renders the bounded result.**

Design / acceptance: `DIFF_VIEWER.md`, `STEP_4_5_ACCEPTANCE.md`.

## Step 4.6 — Run metrics

Accepted boundary:

> **Typed evidence decides runtime truth; metrics only summarize that accepted truth.**

Product Run Metrics remains a descriptive read model. Step 4.8 extends its raw typed counters for Reviewer rejection, scope violation, and durable Developer/Repair token usage without adding a success formula to Product Run Metrics.

Design / acceptance: `RUN_METRICS.md`, `STEP_4_6_ACCEPTANCE.md`.

## Step 4.7 — GitHub branch + Draft PR publication

Accepted boundary:

> **Accepted runtime/Git evidence selects what may be published; GitHub only receives that already-accepted projection.**

Design / acceptance: `GITHUB_PUBLICATION.md`, `STEP_4_7_ACCEPTANCE.md`.

## Step 4.8 — Benchmark / Demo Suite — ACCEPTED / COMPLETE

Design / acceptance:

- `docs/BENCHMARK_DEMO_SUITE.md`
- `docs/STEP_4_8_ACCEPTANCE.md`
- `benchmarks/v1/demo-suite.json`
- `benchmarks/v1/control-plane-demo.json`

### Accepted evaluation architecture

```text
accepted Product/runtime truth
        ↓
bounded typed observations
        ↓
deterministic comparison
        ↓
read-only descriptive report
```

The benchmark records Development Plan evaluation metrics without promoting any aggregate into runtime authority:

- task success rate;
- first-pass success rate;
- repaired success rate;
- average repair/retry count;
- Reviewer rejection rate;
- scope violations detected;
- mean/median terminal latency;
- Developer/Repair prompt/completion/total token usage where durably available;
- model cost only when authoritative data exists.

Current token/cost availability is explicit:

```text
token_usage = PARTIAL
token_usage_scope = DEVELOPER_REPAIR_ONLY
reviewer token usage = NOT_AVAILABLE
estimated model cost = NOT_AVAILABLE
```

### Required V1 deterministic demos

```text
NORMAL_SUCCESS
SCOPE_VIOLATION
REVIEW_REPAIR
INVALID_AGENT_OUTPUT
PARALLEL_CONFLICT
```

The hardened Parallel Conflict demo runs two tasks concurrently through `ParallelWorkerCoordinator`, creates isolated worktree commits, enters `TopologicalMergeQueue`, produces a real Git conflict, and structurally classifies it without corrupting the base workspace.

The hardened Normal Success demo uses a protected golden pytest executed through the default Docker `DeterministicVerifier`, so the demonstration proves both scoped implementation and production-equivalent deterministic verification.

### Deterministic/live separation

```text
devflow-benchmark demo      deterministic control-plane CI harness
devflow-benchmark run       stochastic live Product API / SiliconFlow benchmark
devflow-benchmark evaluate  deterministic offline reevaluation
```

Live runs record an operator-declared experiment identity: runtime commit, provider, role-model mapping, context strategy, and verifier identity. CI requires no paid SiliconFlow call.

### Step 4.8 implementation-head acceptance evidence

Final hardening implementation head before the acceptance-ledger commit:

`f07e95fbfbcb6d5179925c41ce573d2cb63cd47d`

Backend Quality:

- PostgreSQL + Redis: **PASS**;
- Alembic upgrade → downgrade base → re-upgrade: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **361 passed in 29.50s**.

Frontend Quality on the same implementation head:

- locked install: **PASS**;
- TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **PASS**;
- Vite production build: **PASS**.

The final hardening pass also removed a pre-existing lease-lifecycle test race: heartbeat is now exercised while generation 1 is comfortably live, then the renewed short lease is deliberately allowed to expire before takeover. Production lease renewal/fencing semantics were not relaxed.

PR #33 merge review had no unresolved review threads after hardening.

The acceptance/progress ledger paths themselves trigger both Backend Quality and Frontend Quality, so the final ledger head must independently pass both workflows before merge.

Frozen Step 4.8 boundary:

> **The benchmark may measure success, repair, failure, latency, and available usage evidence; only the accepted runtime decides what actually happened.**

---

# V1.0 — ACCEPTED / COMPLETE

With Steps 4.1 through 4.8 accepted, **Phase 4 — V1.0 Productization is ACCEPTED / COMPLETE**.

V1.0 provides one coherent evidence-driven product path:

```text
browser request
        ↓
validated Product API
        ↓
persisted TaskContract / DAG truth
        ↓
Dramatiq execution + lease/run_token fencing
        ↓
controlled Developer / Repair tools
        ↓
Git scope + deterministic verification
        ↓
independent Reviewer
        ↓
parallel worktrees / merge queue / Integration-Human Gate
        ↓
persisted typed evidence + monotonic runtime events
        ↓
SSE / DAG / Diff / Metrics read projections
        ↓
evidence-bound GitHub Draft PR publication
        ↓
versioned benchmark/demo evaluation
```

No browser state, benchmark aggregate, LLM self-report, event message, or publication result can replace the accepted runtime evidence chain.

---

# Phase 5 — V1.1 Durable Agent Runtime — IN PROGRESS

Phase 5 is a deliberately separate Post-V1.0 roadmap. It strengthens long-running execution and crash recovery instead of extending the V1.0 productization scope.

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | typed read-only durable recovery projection; implementation head `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`; Backend 377 tests + 5/5 V1 demos |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL-first REQUESTED/ENQUEUED/PUBLISH_FAILED ledger; crash ambiguity preserved; implementation head `30e92051aeae00e674010b66ecb8da329c793e0a`; Backend 380 tests + 5/5 V1 demos |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked prepare + publication revalidation; concurrent one-send; generation N→N+1 remains lease-owned; implementation head `0d7586405853f19923b906b782ac6ab167886ffe`; Backend 385 tests + 5/5 V1 demos |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | persisted-DAG frontier reconstruction + integration-aware execution bases + Step 5.3-only dispatch authority; implementation head `16be10b49a563429f459e337410bcb2c94a1d3ae`; Backend 393 tests + 5/5 V1 demos |
| 5.5 | Durable Human Pause / Resume | **NEXT / NOT STARTED** | — |
| 5.6 | Causal Trace Correlation | **NOT STARTED** | — |
| 5.7 | Operator Recovery / Approval Surface | **NOT STARTED** | — |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **NOT STARTED** | — |

## Step 5.1 — Recovery State Classifier — ACCEPTED / COMPLETE

Accepted architecture:

```text
PersistedRunSnapshot
        +
TaskLeaseSnapshot
        +
validated WORKER_EXECUTION / DISPATCH_EVENT evidence
        ↓
RecoveryStateClassifier
        ↓
TaskRecoveryAssessment
        ↓
RunRecoveryPlan
```

Accepted dispositions:

```text
NO_ACTION_RUN_TERMINAL
WAIT_ACTIVE_OWNER
RESUME_FROM_TERMINAL_EVIDENCE
REDISPATCH_CANDIDATE_EXPIRED_GENERATION
BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
BLOCKED_RELEASED_EVIDENCE_GAP
```

Step 5.1 performs no enqueue, lease mutation, evidence append, scheduler transition, Git mutation, Run finalization, or GitHub publication. A redispatch candidate remains a diagnosis only; future mutation requires fresh locked PostgreSQL revalidation.

The final PostgreSQL integration hardening proves `RecoveryInspector` composes the real `PostgresEvidenceStore` and `PostgresTaskLeaseStore` without modifying Run truth, runtime events, lease ownership, generation, dispatch identity, or lifecycle timestamps.

Implementation-head Backend Quality evidence:

- exact head: `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`;
- PostgreSQL + Redis: **PASS**;
- Alembic round trip: **PASS**;
- verifier image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **377 passed in 35.41s**.

Design / acceptance:

- `docs/V1_1_ROADMAP.md`
- `docs/DURABLE_RECOVERY.md`
- `docs/STEP_5_1_ACCEPTANCE.md`

## Step 5.2 — Durable Dispatch Attempt Ledger — ACCEPTED / COMPLETE

Accepted architecture:

```text
persisted RUNNING Run + Task
        ↓
PostgreSQL REQUESTED
        ↓ commit before broker
Dramatiq actor.send(stable dispatch_id)
        ↓
┌────────────────────────────┬──────────────────────────────┐
│ acknowledgement observed   │ BrokerConnectionError        │
↓                            ↓
ENQUEUED                     PUBLISH_FAILED
```

If a process crashes after the broker may have accepted the message but before PostgreSQL can record the acknowledgement, the durable row remains `REQUESTED`. That state is an explicit unknown publication outcome, not a fabricated failure and not retry authorization.

Accepted guarantees:

- one stable `dispatch_id` per publication attempt;
- PostgreSQL intent is committed before broker publication;
- ordinary dispatch creates only attempt 1;
- competing initial attempts for one Task serialize and fail closed;
- exact ENQUEUED replay reconstructs a receipt without a second broker call;
- REQUESTED/PUBLISH_FAILED replay never calls the broker implicitly;
- terminal broker/failure facts are immutable;
- known broker failure is durable but is not treated as proof of non-delivery;
- PostgreSQL/Redis atomicity and exactly-once delivery are explicitly not claimed.

Implementation-head acceptance evidence:

- exact head: `30e92051aeae00e674010b66ecb8da329c793e0a`;
- PostgreSQL + Redis: **PASS**;
- Alembic `0001 → 0007 → base → 0007`: **PASS**;
- verifier image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **380 passed in 28.15s**;
- Frontend Quality: **PASS**;
- PR #35 review threads at acceptance: **0 unresolved**.

Design / acceptance:

- `docs/DURABLE_DISPATCH_LEDGER.md`
- `docs/STEP_5_2_ACCEPTANCE.md`

Frozen Step 5.2 boundary:

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

## Step 5.3 — Idempotent Task Reconciler — ACCEPTED / COMPLETE

Accepted architecture:

```text
recovery candidate
        ↓
Txn A: lock + fresh DB-time revalidation
        ↓
durable REQUESTED
        ↓ commit
Txn B: re-lock + fresh revalidation
        ↓
hold authority locks across broker send
        ↓
ENQUEUED / PUBLISH_FAILED
```

Accepted guarantees:

- a stale Step 5.1 diagnosis is never mutation authorization;
- concurrent reconcilers publish at most one fresh dispatch for one Task;
- ACTIVE owners are never raced by recovery;
- EXPIRED generations without terminal worker evidence receive at most one fresh dispatch identity;
- existing REQUESTED/PUBLISH_FAILED histories are not implicitly republished;
- accepted terminal worker evidence is resumed rather than rerun;
- worker evidence must be hash-valid, typed, and bound to durable dispatch history;
- recovered ownership still flows through `PostgresTaskLeaseStore.acquire_task_lease()`;
- generation advances N → N+1 only through that existing lease authority;
- generation N+1 receives a fresh `run_token`;
- the previous token remains fenced after takeover;
- failure injection preserves `lease_acquired_at < heartbeat_at < lease_until < observed_at`; production lease validation was not weakened.

Implementation-head acceptance evidence:

- exact head: `0d7586405853f19923b906b782ac6ab167886ffe`;
- PostgreSQL + Redis: **PASS**;
- Alembic `0001 → 0007 → base → 0007`: **PASS**;
- verifier image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **385 passed in 35.08s**;
- Frontend Quality: **PASS**;
- PR #36 review threads at implementation acceptance: **0 unresolved**.

Design / acceptance:

- `docs/IDEMPOTENT_RECONCILER.md`
- `docs/STEP_5_3_ACCEPTANCE.md`

Frozen Step 5.3 boundary:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

Next authority transition:

```text
validated persisted DAG
        +
accepted terminal task facts
        +
fresh per-task ownership/recovery authority
        ↓
Step 5.4 reconstructs the legal scheduling frontier
        ↓
actual dispatch remains delegated to Step 5.3
```

Step 5.4 must not become a second scheduler truth: completed dependencies must never rerun, downstream tasks become eligible only after all accepted dependencies succeed, and every mutation must retain Step 5.3's fresh per-Task revalidation.

## Step 5.4 — DAG-wide Run Reconciliation — ACCEPTED / COMPLETE

Accepted architecture:

```text
validated persisted DAG
        +
accepted terminal WORKER_EXECUTION facts
        +
fresh DB-time ownership facts
        +
accepted cumulative integration evidence
        ↓
reconstruct legal DAG frontier
        ↓
prove dependency-aware execution base
        ↓
delegate candidates to Step 5.3
```

Accepted guarantees:

- no second mutable/persisted scheduler truth was introduced;
- `TaskDAG` remains the dependency/ready/blocked authority;
- successful terminal Tasks are completed facts and never rerun by recovery;
- failed terminal Tasks block descendants through existing DAG semantics;
- ACTIVE ownership is never raced;
- RELEASED ownership with missing terminal evidence remains blocked rather than silently reacquired;
- a DAG-ready dependent Task waits until accepted integration evidence proves its code base contains all direct dependencies;
- root Tasks use the frozen Run base;
- dependent Tasks use an evidence-bound integration head;
- merge snapshots are cumulative, deterministic, bounded, and cross-checked against successful Worker commit pairs;
- managed Git revalidates exact task and integration parent chains;
- queue payloads do not carry base SHA, branch, lease generation, or `run_token`;
- multi-task queued workers fail closed without the dependency-aware base resolver;
- only `RECONCILE_CANDIDATE` Tasks are nominated to Step 5.3;
- Step 5.3 still performs fresh locked mutation authorization and provides per-Task exactly-one fresh publication under concurrency.

Implementation-head acceptance evidence:

- exact head: `16be10b49a563429f459e337410bcb2c94a1d3ae`;
- PostgreSQL + Redis: **PASS**;
- Alembic `0001 → 0007 → base → 0007`: **PASS**;
- no Step 5.4 database migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **393 passed in 35.07s**.

Documentation/workflow hardening head `04b8d735b218d17196c766a725985f6115c3fe9f` independently passed Backend Quality with **393 passed in 39.44s** and Frontend Quality with locked install, typecheck, lint, tests, and production build.

PR #37 merge review found no unresolved review threads and confirmed production composition injects managed-Git execution-base verification. Persisted evidence is loaded in ascending evidence-id order, so cumulative integration history has a deterministic durable order.

Design / acceptance:

- `docs/DAG_RUN_RECONCILIATION.md`
- `docs/STEP_5_4_ACCEPTANCE.md`

Frozen Step 5.4 boundary:

> **Run reconciliation may reconstruct the scheduling frontier from validated DAG and accepted terminal task facts; it may not create a second scheduler truth.**

Next authority transition:

```text
reconstructed legal scheduling frontier
        +
intentional Human pause fact
        +
accepted Human resume/decision evidence
        ↓
Step 5.5 must distinguish paused work from abandoned work
        ↓
recovery may resume only when durable policy permits
```

Step 5.5 must not represent an intentional Human Gate pause as an expired/abandoned worker generation, and browser/operator intent must not become durable resume authority until it has crossed an authenticated, typed, persisted decision boundary.
