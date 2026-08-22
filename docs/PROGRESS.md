# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. Detailed design, hardening history, failure evidence, authority analysis, and exact CI evidence remain in the corresponding design/acceptance documents, pull requests, workflow runs, and Git history.

## Current position

- Current product milestone: **Phase 6 — Autonomous Multi-Agent Product Loop — ACCEPTED / COMPLETE**
- Current durable-runtime item: **Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance — ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**
- Phase 1 status: **ACCEPTED / COMPLETE**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **ACCEPTED / COMPLETE**
- Phase 4 status: **ACCEPTED / COMPLETE**
- Phase 5 status: **IN PROGRESS** — Steps 5.1–5.7 accepted; Step 5.8 implementation complete and awaiting ledger acceptance
- Phase 6 status: **ACCEPTED / COMPLETE**
- V0.1 status: **ACCEPTED / COMPLETE**
- V0.2 status: **ACCEPTED / COMPLETE**
- V0.3 status: **ACCEPTED / COMPLETE**
- V1.0 status: **ACCEPTED / COMPLETE**
- V1.1 status: **IN PROGRESS / NOT YET ACCEPTED**

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

Frozen Step 5.8 principle:

> **Chaos may perturb delivery, liveness, timing, process continuity, and stale actors; it may not weaken the production authority model or replace production stores with an easier test-only truth.**

---

# Phase 1 — V0.1 Single Task Evidence Loop — ACCEPTED / COMPLETE

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

Accepted boundary: validated DAG dependencies decide scheduling order, task output is isolated in Git worktrees, integration is topological/evidence-bound, and conflicts are structured before any Human/repair authority is considered.

---

# Phase 3 — V0.3 Safety, Context and Reliability — ACCEPTED / COMPLETE

| Step | Capability | Acceptance snapshot |
| --- | --- | --- |
| 3.1 | Docker Verification Sandbox + Resource Limits | 220 tests |
| 3.2 | Context Packet Builder | 228 tests |
| 3.3 | AST / Import-aware Relevant-code Extraction | 240 tests |
| 3.4 | PostgreSQL Persistence | 248 tests |
| 3.5 | Redis + Dramatiq Workers | 258 tests |
| 3.6 | Lease + Heartbeat | 270 tests |
| 3.7 | `run_token` stale-write fencing | 273 tests |
| 3.8 | Structured runtime events | 281 tests |

Phase 3 completion commit:

`774ac16bfedcc8d02690ef803fd8f1eee6593158`

Frozen authority:

> **Lease establishes liveness; `run_token` establishes write authority.**

> **Typed evidence decides what happened; structured events make that accepted history queryable.**

---

# Phase 4 — V1.0 Productization — ACCEPTED / COMPLETE

| Step | Capability | Acceptance snapshot |
| --- | --- | --- |
| 4.1 | React / TypeScript UI Foundation | locked install + typecheck + lint + UI tests + production build |
| 4.2 | Project / New Run / Dashboard / Task Detail | Backend 293 tests; Frontend green |
| 4.3 | SSE live status/log updates | PostgreSQL sequence → SSE/Last-Event-ID; Backend 299 tests |
| 4.4 | DAG visualization | hash-validated persisted DAG + read-only SVG; Backend 307 tests |
| 4.5 | Diff viewer | evidence-selected commit pairs + bounded read-only Git diff; Backend 317 tests |
| 4.6 | Run metrics | descriptive typed evidence/event projection; Backend 322 tests |
| 4.7 | GitHub branch + Draft PR integration | evidence-bound/fenced publication projection; Backend 332 tests |
| 4.8 | Benchmark / Demo Suite | versioned fixtures + 5 deterministic demos; Backend 361 tests |

Step 4.8 implementation head:

`f07e95fbfbcb6d5179925c41ce573d2cb63cd47d`

Accepted browser boundary:

> **The browser may present accepted runtime truth; it may not manufacture runtime truth.**

---

# Phase 5 — V1.1 Durable Agent Runtime — ACCEPTANCE CANDIDATE

| Step | Capability | Status | Acceptance snapshot |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | read-only durable recovery projection; implementation `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`; Backend 377 tests + 5/5 demos |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL-first REQUESTED/ENQUEUED/PUBLISH_FAILED; implementation `30e92051aeae00e674010b66ecb8da329c793e0a`; Backend 380 tests + 5/5 demos |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked prepare/publication revalidation; implementation `0d7586405853f19923b906b782ac6ab167886ffe`; Backend 385 tests + 5/5 demos |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | persisted-DAG frontier + evidence-bound execution bases + Step 5.3 dispatch authority; implementation `16be10b49a563429f459e337410bcb2c94a1d3ae`; Backend 393 tests + 5/5 demos |
| 5.5 | Durable Human Pause / Resume | **ACCEPTED / COMPLETE via Phase 6** | durable typed Human decision + restart-safe bounded repair; hardening `6673d87a3aea15a68196a15818a109e74046d1d7` |
| 5.6 | Causal Trace Correlation | **ACCEPTED / COMPLETE** | metadata-only trace + Run→Task→Dispatch→Generation correlation; implementation `e4942113ae5d10283cb31e6be3f832d04a782b61`; Backend 412 tests + 5/5 demos |
| 5.7 | Operator Recovery / Approval Surface | **ACCEPTED / COMPLETE** | opaque server action + fresh revalidation + dispatch-aware duplicate suppression; implementation `434f5d2704afe3428366dd5e0406b8e061d52640`; accepted-state head `5c8e72a46e3c927fbcbfd304b5f2a95e4b249006` |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED** | ten-domain versioned chaos matrix; implementation/hardening `572995329fb0422bc2de72d83db6096cda70c8d6`; Backend 10/10 chaos + 442 tests; Frontend green |

## Step 5.1–5.4 accepted recovery foundation

Steps 5.1–5.4 established the durable recovery control plane:

```text
PostgreSQL Run / Task / DAG truth
        +
Dispatch-attempt ledger
        +
DB-time lease/generation
        +
run_token fencing
        ↓
read-only recovery classification
        ↓
fresh locked task reconciliation
        ↓
DAG-wide frontier reconstruction
```

Recovery diagnosis never authorizes mutation by itself.

## Step 5.5 — Durable Human Pause / Resume — ACCEPTED / COMPLETE via Phase 6

Accepted guarantees include pending-gate reconstruction, typed Human decisions, exact Git/policy/evidence binding, bounded repair, deterministic verification, and crash/GC-safe repair staging refs.

## Step 5.6 — Causal Trace Correlation — ACCEPTED / COMPLETE

Trace is metadata-only diagnostic correlation. It cannot schedule, recover, repair, verify, merge, finalize, or publish.

Implementation head:

`e4942113ae5d10283cb31e6be3f832d04a782b61`

Candidate ledger:

`f0ca4d5f76cfc376d65cbc342648f01a8faf4939`

Design / acceptance: `CAUSAL_TRACE_CORRELATION.md`, `STEP_5_6_ACCEPTANCE.md`.

## Step 5.7 — Operator Recovery / Approval Surface — ACCEPTED / COMPLETE

Accepted architecture:

```text
Recovery + durable dispatch facts
        ↓
server-issued opaque action_id
        ↓
Operator requests ADVANCE_RUN
        ↓
fresh server-side plan rebuild
        ↓
Step 5.4 / Step 5.3 authority or reject stale
```

Implementation/hardening head:

`434f5d2704afe3428366dd5e0406b8e061d52640`

Candidate ledger head:

`030b50b2c122155fed7b53ecbe352550b9c79dd9`

Accepted-state head:

`5c8e72a46e3c927fbcbfd304b5f2a95e4b249006`

Design / acceptance: `OPERATOR_RECOVERY_SURFACE.md`, `STEP_5_7_ACCEPTANCE.md`.

## Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance — ACCEPTANCE CANDIDATE

Step 5.8 adds no recovery authority. It creates a versioned, fail-closed manifest and executes ten deterministic fault domains through accepted production authority components.

Manifest:

`benchmarks/v1_1/chaos-recovery.json`

Candidate suite:

- schema version `1`;
- suite version `0.2.0`;
- suite SHA-256 `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`;
- ten required fault domains;
- seven frozen invariants.

Fault matrix:

```text
C01 ACTIVE worker loss
C02 stale evidence write
C03 stale Git mutation
C04 terminal evidence / controller crash
C05 duplicate reconciler race
C06 broker publish failure
C07 pending Human Gate restart
C08 DAG resume without completed-work rerun
C09 concurrent Operator ADVANCE_RUN race
C10 repair persisted / crash before integration Git CAS
```

Frozen invariants:

- at most one legal mutation;
- stale generation stays fenced;
- unknown state is never guessed;
- failure is never represented as success;
- completed semantic work is not rerun by default;
- Human decision/gate state is durable;
- observability/benchmark/operator projection never becomes authority.

Dedicated Backend gate:

```text
5/5 V1 deterministic demos
        ↓
10/10 V1.1 chaos scenarios
        ↓
full pytest regression
```

Implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Backend Quality #956 (`32559638164`): **PASS** — migrations, verifier image, Ruff, V1 fixtures, **5/5 demos**, **10/10 chaos**, **442 passed, 1 warning in 42.41s**.

Frontend Quality #255 (`32559638135`): **PASS**.

Design / candidate acceptance:

- `docs/CHAOS_RECOVERY_BENCHMARK.md`
- `docs/STEP_5_8_ACCEPTANCE.md`
- `docs/V1_1_ROADMAP.md`

Step 5.8 and V1.1 are **not accepted yet**. This complete candidate ledger must independently pass Backend + Frontend Quality. Only then may Step 5.8, Phase 5, and V1.1 transition to `ACCEPTED / COMPLETE`; the accepted-state head must pass both workflows once more.

---

# Phase 6 — Autonomous Multi-Agent Product Loop — ACCEPTED / COMPLETE

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

Step 5.8 has reached **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**.

The implementation/hardening head `572995329fb0422bc2de72d83db6096cda70c8d6` independently passed strict Backend and Frontend quality gates, including the dedicated ten-scenario chaos matrix.

The current candidate ledger must now independently pass both workflows. If it does, the only allowed next repository mutation is the final status transition:

```text
Step 5.8 — ACCEPTED / COMPLETE
Phase 5 — ACCEPTED / COMPLETE
V1.1 — ACCEPTED / COMPLETE
```

That accepted-state head must then pass Backend Quality and Frontend Quality once more. Until that happens, V1.1 remains **IN PROGRESS / NOT YET ACCEPTED**.

PR #41 remains Draft and unmerged throughout this sequence. Its base is temporarily `main` for strict CI and must be restored to `phase5/operator-recovery-surface` only after the accepted-state double-green result.
