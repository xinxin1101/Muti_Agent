# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines what should be built; this ledger records what has actually passed acceptance. Detailed design, hardening history, failure evidence, and authority analysis remain in the corresponding design/acceptance documents, pull requests, CI runs, and Git history.

## Current position

- Current product milestone: **Phase 6 — Autonomous Multi-Agent Product Loop — ACCEPTED / COMPLETE**
- Current durable-runtime item: **Step 5.6 — Causal Trace Correlation — IMPLEMENTATION ACCEPTED / FINAL LEDGER CI PENDING**
- Next durable-runtime backlog item after 5.6 acceptance: **Step 5.7 — Operator Recovery / Approval Surface**
- Phase 1 status: **ACCEPTED / COMPLETE**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **ACCEPTED / COMPLETE**
- Phase 4 status: **ACCEPTED / COMPLETE**
- Phase 5 status: **IN PROGRESS** — 5.1–5.5 accepted; 5.6 implementation accepted and awaiting final ledger CI; 5.7–5.8 remain unaccepted backlog
- Phase 6 status: **ACCEPTED / COMPLETE**
- V0.1 status: **ACCEPTED / COMPLETE**
- V0.2 status: **ACCEPTED / COMPLETE**
- V0.3 status: **ACCEPTED / COMPLETE**
- V1.0 status: **ACCEPTED / COMPLETE**
- V1.1 status: **IN PROGRESS**

Frozen project principle:

> **Agents propose; evidence decides.**

Frozen V1.1 principle:

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

Frozen Phase 6 product principle:

> **A user may request work in natural language; scheduling, execution, integration, repair, success, diff and publication remain server-owned evidence decisions.**

Frozen Step 5.6 principle:

> **Trace may explain accepted runtime history; it may not decide, repair, schedule, resume, verify, merge, finalize, or publish that history.**

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

Phase 1 established:

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

Accepted boundary:

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

Accepted runtime foundation:

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

---

# Phase 4 — V1.0 Productization — ACCEPTED / COMPLETE

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

Accepted product read/projection boundary:

> **The browser may present accepted runtime truth; it may not manufacture runtime truth.**

Step 4.8 deterministic demo set remains:

```text
NORMAL_SUCCESS
SCOPE_VIOLATION
REVIEW_REPAIR
INVALID_AGENT_OUTPUT
PARALLEL_CONFLICT
```

Step 4.8 implementation head:

`f07e95fbfbcb6d5179925c41ce573d2cb63cd47d`

Backend acceptance: **361 passed in 29.50s + 5/5 demos**. Frontend Quality: **PASS**.

V1.0 coherent product path:

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

Phase 5 strengthens long-running execution, crash recovery, causal observability, operator intervention, and recovery validation without creating parallel runtime truth.

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | typed read-only durable recovery projection; implementation head `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`; Backend 377 tests + 5/5 demos |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL-first REQUESTED/ENQUEUED/PUBLISH_FAILED ledger; implementation head `30e92051aeae00e674010b66ecb8da329c793e0a`; Backend 380 tests + 5/5 demos |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked prepare/publication revalidation; implementation head `0d7586405853f19923b906b782ac6ab167886ffe`; Backend 385 tests + 5/5 demos |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | persisted-DAG frontier + integration-aware execution bases + Step 5.3-only dispatch authority; implementation head `16be10b49a563429f459e337410bcb2c94a1d3ae`; Backend 393 tests + 5/5 demos |
| 5.5 | Durable Human Pause / Resume | **ACCEPTED / COMPLETE via Phase 6** | durable typed Human decision + restart-safe bounded repair; hardening head `6673d87a3aea15a68196a15818a109e74046d1d7` |
| 5.6 | Causal Trace Correlation | **IMPLEMENTATION ACCEPTED / FINAL LEDGER CI PENDING** | metadata-only `TRACE_BATCH` + durable Run→Task→Dispatch→Generation correlation + read-only projector/API; implementation head `e4942113ae5d10283cb31e6be3f832d04a782b61`; Backend 412 tests + 5/5 demos; Frontend green |
| 5.7 | Operator Recovery / Approval Surface | **PARTIAL / NOT ACCEPTED** | Phase 6 exposes Human Gate approval UI/API, but broader recovery/operator controls remain open |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **PARTIAL / NOT ACCEPTED** | Phase 6 covers repair DB→Git CAS crash/GC recovery, not the full V1.1 chaos matrix |

## Step 5.1 — Recovery State Classifier — ACCEPTED / COMPLETE

Accepted boundary:

> **Recovery classification may explain durable state; it may not mutate or redispatch work.**

Implementation head `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`: **377 passed + 5/5 demos**.

Design / acceptance: `DURABLE_RECOVERY.md`, `STEP_5_1_ACCEPTANCE.md`.

## Step 5.2 — Durable Dispatch Attempt Ledger — ACCEPTED / COMPLETE

Accepted boundary:

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

Implementation head `30e92051aeae00e674010b66ecb8da329c793e0a`: **380 passed + 5/5 demos**.

Design / acceptance: `DURABLE_DISPATCH_LEDGER.md`, `STEP_5_2_ACCEPTANCE.md`.

## Step 5.3 — Idempotent Task Reconciler — ACCEPTED / COMPLETE

Accepted boundary:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

Implementation head `0d7586405853f19923b906b782ac6ab167886ffe`: **385 passed + 5/5 demos**.

Design / acceptance: `IDEMPOTENT_RECONCILER.md`, `STEP_5_3_ACCEPTANCE.md`.

## Step 5.4 — DAG-wide Run Reconciliation — ACCEPTED / COMPLETE

Accepted boundary:

> **Run reconciliation may reconstruct the scheduling frontier from validated DAG and accepted terminal task facts; it may not create a second scheduler truth.**

Implementation head `16be10b49a563429f459e337410bcb2c94a1d3ae`: **393 passed + 5/5 demos**. Documentation/workflow hardening head `04b8d735b218d17196c766a725985f6115c3fe9f` independently passed Backend and Frontend Quality.

Design / acceptance: `DAG_RUN_RECONCILIATION.md`, `STEP_5_4_ACCEPTANCE.md`.

## Step 5.5 — Durable Human Pause / Resume — ACCEPTED / COMPLETE via Phase 6

Phase 6 closed the Human-pause authority required by the autonomous product loop.

Accepted guarantees include:

- pending Integration/Human Gate identity is durably reconstructible;
- typed Human decisions survive API/worker restarts;
- resume revalidates exact Git conflict, task commit, integration head, policy fingerprint and decision binding;
- `AUTHORIZE_REPAIR` grants only bounded conflict-path repair authority;
- deterministic verification gates repaired merged code;
- typed `INTEGRATION_REPAIR` and matching Human decision evidence are required downstream;
- server-owned repair staging refs preserve crash/GC object liveness;
- fresh services can reuse exact persisted repair commits without rerunning the Agent;
- staging refs are cleanup/liveness mechanisms, not runtime truth.

Hardening head `6673d87a3aea15a68196a15818a109e74046d1d7` passed strict Backend and Frontend quality gates.

## Step 5.6 — Causal Trace Correlation — IMPLEMENTATION ACCEPTED / FINAL LEDGER CI PENDING

Accepted implementation architecture:

```text
Persisted Run / Tasks / DAG
        +
Durable dispatch attempts
        +
Lease/runtime events
        +
metadata-only TRACE_BATCH
        +
accepted typed runtime/integration evidence
        ↓
CausalTraceProjector
        ↓
RUN
 └─ TASK
     └─ DISPATCH
         └─ GENERATION
             ├─ AGENT_TURN
             │   └─ TOOL_CALL
             ├─ VERIFICATION
             ├─ REVIEW
             ├─ REPAIR
             └─ WORKER_EXECUTION
     └─ INTEGRATION
```

Step 5.6 implementation guarantees:

- trace is diagnostic-only and never becomes scheduler/recovery/success authority;
- `TRACE_BATCH` stores metadata, not model/tool transcripts;
- Developer, Reviewer and Repair production classes emit model-turn spans when a collector exists;
- Reviewer schema-repair is represented as a distinct turn;
- controlled Tool calls are children of the Agent turn that requested them;
- deterministic verification invocations are traced independently;
- the Orchestrator preserves exact legacy Agent call shapes when `trace=None`;
- dispatch/generation correlation is reconstructed from durable server-owned facts;
- `TRACE_BATCH.generation` must agree with durable lease events or projection fails closed;
- `run_token` remains write authority and never enters trace payload/API output;
- PostgreSQL TRACE persistence inherits existing typed/hash/fencing/idempotency boundaries;
- trace persistence failure is isolated and cannot convert task success into failure or authorize retry;
- Product API `GET /api/v1/runs/{run_id}/trace` accepts no browser-authored correlation selectors;
- bounded/corrupt trace projection fails as observability failure without mutating Run truth.

Privacy contract excludes:

```text
raw prompts
raw completions
Tool arguments
Tool result bodies
repository contents/diffs
verifier stdout/stderr bodies
credentials/API keys
run_token
```

Implementation head before ledger commits:

`e4942113ae5d10283cb31e6be3f832d04a782b61`

Backend Quality run #919 (`32497790074`):

- PostgreSQL + Redis: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- no Step 5.6 migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **412 passed in 37.91s**.

Frontend Quality run #212 (`32497790067`): **PASS** for locked install, typecheck, lint, tests and production build.

Design / acceptance:

- `docs/CAUSAL_TRACE_CORRELATION.md`
- `docs/STEP_5_6_ACCEPTANCE.md`

Final acceptance rule:

> The ledger head containing the Step 5.6 design, acceptance status, `PROGRESS.md`, and both workflow path gates must independently pass Backend Quality and Frontend Quality before Step 5.6 becomes fully **ACCEPTED / COMPLETE**.

---

# Phase 6 — Autonomous Multi-Agent Product Loop — ACCEPTED / COMPLETE

Phase 6 connects the accepted runtime into the user-facing natural-language path:

```text
repository + natural-language requirement
        ↓
Planner proposal
        ↓
validated + persisted TaskDAG
        ↓
durable root dispatch
        ↓
parallel generation-bound worker commits
        ↓
evidence-bound topological integration
        ↓
Step 5.4 / Step 5.3 downstream reconciliation
        ↓
Durable Human Gate / bounded repair when required
        ↓
repair-aware terminal completion
        ↓
DAG / Diff / publication projections
        ↓
GitHub Draft PR
```

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 6.1 | Natural-language Run entry | **ACCEPTED / COMPLETE** | New Run uses Project + requirement; browser no longer manufactures TaskContract authority |
| 6.2 | Planner → validated TaskDAG | **ACCEPTED / COMPLETE** | bounded multi-task structured planning + schema repair + TaskDAG validation |
| 6.3 | Persist Run + DAG before dispatch | **ACCEPTED / COMPLETE** | frozen managed Git base + validated DAG persisted before root publication |
| 6.4 | Durable DAG Controller | **ACCEPTED / COMPLETE** | event-driven `advance(run_id)` reconstructs from PostgreSQL/Git; no resident scheduler truth |
| 6.5 | Parallel execution + automatic integration | **ACCEPTED / COMPLETE** | generation-bound worktrees + topological merge history + Step 5.4-only downstream unlock |
| 6.6 | Durable Human Pause / Resume + bounded repair | **ACCEPTED / COMPLETE** | typed Human authority + repair-aware completion/diff/publication + staging-ref GC crash recovery |
| 6.7 | Full Autonomous Product E2E | **ACCEPTED / COMPLETE** | real Git + PostgreSQL 3-task E2E; implementation head `e4f12f2fe0f90b2d789f242f22d4b7b3a9126108`; Backend 402 tests + 5/5 demos; Frontend green |

Step 6.7 implementation-head Backend Quality run #903:

- PostgreSQL + Redis: **PASS**;
- Alembic round trip: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **402 passed in 38.03s**.

Frontend Quality run #198: **PASS**.

The deterministic Phase 6 E2E replaces only external nondeterminism with fixed test boundaries; it retains production Git worktree/commit/merge logic, PostgreSQL evidence/dispatch stores, lease/`run_token` authority, Step 5.4 DAG reconciliation, Step 5.3 idempotent publication authority, repair-aware terminal completion, integration diff reconstruction and publication-source persistence.

Design / acceptance:

- `docs/AUTONOMOUS_MULTI_AGENT_PRODUCT_LOOP.md`
- `docs/STEP_6_7_ACCEPTANCE.md`

Frozen Phase 6 boundary:

> **Natural-language intent may start the Run; only validated and persisted server-side facts may advance or finish it.**

Remaining V1.1 work after Step 5.6 final ledger acceptance:

```text
Phase 5.7  broader Operator Recovery / Approval Surface    NOT ACCEPTED
Phase 5.8  full Chaos / Recovery Benchmark                NOT ACCEPTED
```

These remain separate from the already-accepted Phase 6 Autonomous Multi-Agent Product Loop.

---

# Acceptance gate now in progress

The Step 5.6 implementation head is green. This ledger commit intentionally does **not** yet declare Step 5.6 fully accepted.

Required final gate:

```text
Step 5.6 design ledger
        +
Step 5.6 acceptance ledger
        +
PROGRESS status
        +
Backend/Frontend workflow path gates
        ↓
final ledger head
        ↓
Backend Quality PASS
        +
Frontend Quality PASS
        ↓
Step 5.6 ACCEPTED / COMPLETE
```

After that gate, the next core development target is Step 5.7 — Operator Recovery / Approval Surface. It may consume causal trace only as read-only diagnostic context; every mutating operator action must still receive fresh PostgreSQL/Git/lease/fencing revalidation.
