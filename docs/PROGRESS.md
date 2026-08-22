# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines what should be built; this ledger records what has actually passed acceptance. Detailed design, hardening history, failure evidence, authority analysis, and exact CI evidence remain in the corresponding design/acceptance documents, pull requests, workflow runs, and Git history.

## Current position

- Current product milestone: **Phase 6 — Autonomous Multi-Agent Product Loop — ACCEPTED / COMPLETE**
- Completed durable-runtime item: **Step 5.7 — Operator Recovery / Approval Surface — ACCEPTED / COMPLETE**
- Next durable-runtime backlog item: **Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance**
- Phase 1 status: **ACCEPTED / COMPLETE**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **ACCEPTED / COMPLETE**
- Phase 4 status: **ACCEPTED / COMPLETE**
- Phase 5 status: **IN PROGRESS** — 5.1–5.7 accepted; 5.8 remains unaccepted
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

Frozen Step 5.7 principle:

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

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

Accepted path:

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
- merge conflicts become structured evidence before any repair decision;
- Integration/Human Gate authorization remains evidence-bound.

---

# Phase 3 — V0.3 Safety, Context and Reliability — ACCEPTED / COMPLETE

| Step | Capability | Status | Final snapshot |
| --- | --- | --- | --- |
| 3.1 | Docker Verification Sandbox + Resource Limits | **ACCEPTED** | 220 tests |
| 3.2 | Context Packet Builder | **ACCEPTED** | 228 tests |
| 3.3 | AST / Import-aware Relevant-code Extraction | **ACCEPTED** | 240 tests |
| 3.4 | PostgreSQL Persistence | **ACCEPTED** | 248 tests |
| 3.5 | Redis + Dramatiq Workers | **ACCEPTED** | 258 tests |
| 3.6 | Lease + Heartbeat | **ACCEPTED** | 270 tests |
| 3.7 | `run_token` stale-write fencing | **ACCEPTED** | 273 tests |
| 3.8 | Structured runtime events | **ACCEPTED** | 281 tests |

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
| 4.2 | Project / New Run / Dashboard / Task Detail | **ACCEPTED** | Backend 293 tests; Frontend green |
| 4.3 | SSE live status/log updates | **ACCEPTED** | PostgreSQL sequence → SSE/Last-Event-ID; Backend 299 tests |
| 4.4 | DAG visualization | **ACCEPTED** | hash-validated persisted DAG + read-only SVG; Backend 307 tests |
| 4.5 | Diff viewer | **ACCEPTED** | evidence-selected commit pairs + bounded read-only Git diff; Backend 317 tests |
| 4.6 | Run metrics | **ACCEPTED** | descriptive typed evidence/event projection; Backend 322 tests |
| 4.7 | GitHub branch + Draft PR integration | **ACCEPTED** | evidence-bound/fenced publication projection; Backend 332 tests |
| 4.8 | Benchmark / Demo Suite | **ACCEPTED** | versioned fixtures + 5 deterministic demos; Backend 361 tests |

Step 4.8 implementation head:

`f07e95fbfbcb6d5179925c41ce573d2cb63cd47d`

Accepted browser boundary:

> **The browser may present accepted runtime truth; it may not manufacture runtime truth.**

---

# Phase 5 — V1.1 Durable Agent Runtime — IN PROGRESS

Phase 5 strengthens long-running execution, crash recovery, causal observability, operator intervention, and recovery validation without creating parallel runtime truth.

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | read-only durable recovery projection; implementation `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`; Backend 377 tests + 5/5 demos |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL-first REQUESTED/ENQUEUED/PUBLISH_FAILED; implementation `30e92051aeae00e674010b66ecb8da329c793e0a`; Backend 380 tests + 5/5 demos |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked prepare/publication revalidation; implementation `0d7586405853f19923b906b782ac6ab167886ffe`; Backend 385 tests + 5/5 demos |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | persisted-DAG frontier + evidence-bound execution bases + Step 5.3 dispatch authority; implementation `16be10b49a563429f459e337410bcb2c94a1d3ae`; Backend 393 tests + 5/5 demos |
| 5.5 | Durable Human Pause / Resume | **ACCEPTED / COMPLETE via Phase 6** | durable typed Human decision + restart-safe bounded repair; hardening `6673d87a3aea15a68196a15818a109e74046d1d7` |
| 5.6 | Causal Trace Correlation | **ACCEPTED / COMPLETE** | metadata-only trace + Run→Task→Dispatch→Generation correlation; implementation `e4942113ae5d10283cb31e6be3f832d04a782b61`; Backend 412 tests + 5/5 demos; candidate ledger `f0ca4d5f76cfc376d65cbc342648f01a8faf4939` green |
| 5.7 | Operator Recovery / Approval Surface | **ACCEPTED / COMPLETE** | opaque server action + fresh revalidation + Step 5.4/5.3 delegation + dispatch-aware duplicate suppression; implementation `434f5d2704afe3428366dd5e0406b8e061d52640`; candidate ledger `030b50b2c122155fed7b53ecbe352550b9c79dd9`; Backend 426 tests + 5/5 demos; Frontend green |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **NOT ACCEPTED** | full systematic chaos matrix remains open |

## Step 5.1 — Recovery State Classifier — ACCEPTED / COMPLETE

Accepted boundary:

> **Recovery classification may explain durable state; it may not mutate or redispatch work.**

Design / acceptance: `DURABLE_RECOVERY.md`, `STEP_5_1_ACCEPTANCE.md`.

## Step 5.2 — Durable Dispatch Attempt Ledger — ACCEPTED / COMPLETE

Accepted boundary:

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

Design / acceptance: `DURABLE_DISPATCH_LEDGER.md`, `STEP_5_2_ACCEPTANCE.md`.

## Step 5.3 — Idempotent Task Reconciler — ACCEPTED / COMPLETE

Accepted boundary:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

Design / acceptance: `IDEMPOTENT_RECONCILER.md`, `STEP_5_3_ACCEPTANCE.md`.

## Step 5.4 — DAG-wide Run Reconciliation — ACCEPTED / COMPLETE

Accepted boundary:

> **Run reconciliation may reconstruct the scheduling frontier from validated DAG and accepted terminal task facts; it may not create a second scheduler truth.**

Design / acceptance: `DAG_RUN_RECONCILIATION.md`, `STEP_5_4_ACCEPTANCE.md`.

## Step 5.5 — Durable Human Pause / Resume — ACCEPTED / COMPLETE via Phase 6

Accepted guarantees include durable pending-gate reconstruction, typed Human decisions, exact Git/policy/evidence revalidation, bounded conflict-path repair, deterministic verification, repair-aware completion, and crash/GC-safe staging refs.

Hardening head:

`6673d87a3aea15a68196a15818a109e74046d1d7`

## Step 5.6 — Causal Trace Correlation — ACCEPTED / COMPLETE

Accepted architecture:

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
RUN → TASK → DISPATCH → GENERATION
                    ├→ AGENT_TURN → TOOL_CALL
                    ├→ VERIFICATION
                    ├→ REVIEW
                    ├→ REPAIR
                    └→ WORKER_EXECUTION
```

Accepted guarantees:

- trace is diagnostic-only and never becomes scheduler/recovery/success authority;
- metadata excludes raw prompts/completions, Tool arguments/results, repository contents, verifier bodies, credentials and `run_token`;
- dispatch/generation correlation is reconstructed from durable server-owned facts;
- inconsistent generation correlation fails closed;
- Product trace API accepts no browser-authored correlation selectors;
- trace persistence failure cannot authorize recovery or convert task success into failure.

Implementation head:

`e4942113ae5d10283cb31e6be3f832d04a782b61`

Candidate ledger:

`f0ca4d5f76cfc376d65cbc342648f01a8faf4939`

Design / acceptance: `CAUSAL_TRACE_CORRELATION.md`, `STEP_5_6_ACCEPTANCE.md`.

## Step 5.7 — Operator Recovery / Approval Surface — ACCEPTED / COMPLETE

Accepted architecture:

```text
Recovery + durable reconciliation facts
        +
dispatch-attempt ledger
        ↓
OperatorRecoveryPlan          ← read-only
        ↓
server-issued opaque action_id
        ↓
Operator chooses ADVANCE_RUN
        ↓
fresh server-side plan rebuild
        ↓
exact action still advertised?
  no → stale / no mutation
  yes
        ↓
typed OPERATOR_ACTION audit
        ↓
Single task → DAGRunReconciler
Multi task  → DurableMultiAgentRunController
        ↓
Step 5.4 / Step 5.3 authority
```

Accepted guarantees:

- browser/Trace never directly authorizes retry;
- only server-advertised `ADVANCE_RUN` exists as the new operator mutation request;
- action fingerprint binds Run/DAG/frontier/lease/worker/execution-base and durable dispatch-attempt facts;
- observation time does not create false staleness;
- fresh plan reconstruction occurs immediately before mutation;
- stale action fails before audit/runtime mutation;
- ACTIVE owner cannot be raced;
- terminal Run cannot be reopened;
- RELEASED evidence gap cannot be silently reacquired;
- a newer dispatch attempt suppresses stale duplicate action advertising before lease acquisition;
- concurrent operator requests against an expired generation produce at most one new broker publication through Step 5.3;
- typed `OPERATOR_ACTION` evidence records the request, not the success outcome;
- Product command endpoint accepts only opaque action id, with no query/body authority selectors;
- frontend renders server `actions[]` only and sends a no-body command;
- existing Human Gate remains the narrower merge-conflict approval authority.

Implementation/hardening head:

`434f5d2704afe3428366dd5e0406b8e061d52640`

Backend Quality #942 (`32557865629`): **PASS** — migrations, verifier image, Ruff, fixture validation, **5/5 demos**, **426 passed, 1 warning in 34.21s**.

Frontend Quality #235 (`32557865642`): **PASS** — locked install, typecheck, lint, tests, production build.

Complete candidate ledger head:

`030b50b2c122155fed7b53ecbe352550b9c79dd9`

Backend Quality #947 (`32558218574`): **PASS** — Alembic round trip, verifier image, Ruff, fixture validation, **5/5 demos**, **426 passed, 1 warning in 39.38s**.

Frontend Quality #240 (`32558218578`): **PASS** — locked install, typecheck, lint, tests, production build.

PR #40 had **0 inline review threads** at the acceptance transition.

Design / acceptance:

- `docs/OPERATOR_RECOVERY_SURFACE.md`
- `docs/STEP_5_7_ACCEPTANCE.md`

Frozen Step 5.7 boundary:

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

The accepted-state ledger head created by this final status transition must independently pass Backend Quality and Frontend Quality. No subsequent document mutation is required merely to copy that head's own CI identifiers back into this file.

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

| Step | Capability | Status |
| --- | --- | --- |
| 6.1 | Natural-language Run entry | **ACCEPTED / COMPLETE** |
| 6.2 | Planner → validated TaskDAG | **ACCEPTED / COMPLETE** |
| 6.3 | Persist Run + DAG before dispatch | **ACCEPTED / COMPLETE** |
| 6.4 | Durable DAG Controller | **ACCEPTED / COMPLETE** |
| 6.5 | Parallel execution + automatic integration | **ACCEPTED / COMPLETE** |
| 6.6 | Durable Human Pause / Resume + bounded repair | **ACCEPTED / COMPLETE** |
| 6.7 | Full Autonomous Product E2E | **ACCEPTED / COMPLETE** |

Step 6.7 implementation head:

`e4f12f2fe0f90b2d789f242f22d4b7b3a9126108`

Accepted Phase 6 boundary:

> **Natural-language intent may start the Run; only validated and persisted server-side facts may advance or finish it.**

Design / acceptance: `AUTONOMOUS_MULTI_AGENT_PRODUCT_LOOP.md`, `STEP_6_7_ACCEPTANCE.md`.

---

# Current acceptance boundary

Step 5.7 is now **ACCEPTED / COMPLETE** because both its implementation/hardening head and its complete candidate ledger independently passed strict Backend and Frontend quality gates before the accepted status was written.

The accepted-state ledger head containing this status must now independently pass both workflows once more. When it does, that external CI result completes the final Step 5.7 acceptance condition without requiring a self-referential follow-up edit.

PR #40 remains Draft and unmerged throughout this sequence.

Next core development target:

**Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance**

Step 5.8 must systematically prove crash/retry/idempotency behavior across PostgreSQL, Redis publication, lease generations, `run_token` fencing, worker evidence, Git objects, integration, repair, and process restarts without fabricating success or duplicate mutation.
