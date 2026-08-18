# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions, failure history, and merge-review hardening remain preserved in the corresponding pull requests, acceptance documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.7 — `run_token` stale-write protection**
- Next item: **Step 3.8 — Structured run/event logs**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.8 status: **NOT STARTED**
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

# Phase 3 — V0.3 Safety, Context and Reliability — IN PROGRESS

| Step | Capability | Status | Merge commit | Final acceptance snapshot |
| --- | --- | --- | --- | --- |
| 3.1 | Docker Verification Sandbox + Resource Limits | **ACCEPTED** | `7387e30e545ca05235dd40d06e6ab3fc15fcfbdd` | 220 tests passed |
| 3.2 | Context Packet Builder | **ACCEPTED** | `2a4d948b29045be3cbcf5ffae4f541d9135457bb` | 228 tests passed |
| 3.3 | AST / Import-aware Relevant-code Extraction | **ACCEPTED** | `02c6f1ecfe0398a339c34621443a1596b73a13de` | 240 tests passed |
| 3.4 | PostgreSQL Persistence | **ACCEPTED** | `577da384072cc882c8851497351d99012de1f653` | 248 tests passed |
| 3.5 | Redis + Dramatiq Workers | **ACCEPTED** | `51048e4818478c07e2c024d78215a43959a56465` | 258 tests passed |
| 3.6 | Lease + Heartbeat | **ACCEPTED** | `c7732d3ad832ff4abd159b322c8f39381159ed83` | 270 tests passed |
| 3.7 | `run_token` stale-write protection | **ACCEPTED** | `31a71bc45b58f8c865a54a972e96678e696f5c66` | 273 tests passed |
| 3.8 | Structured run/event logs | **NEXT / NOT STARTED** | — | — |

Detailed accepted design/acceptance documents:

- Step 3.1: sandbox implementation and tests in PR #18;
- Step 3.2: Context Packet implementation and tests in PR #19;
- Step 3.3: `docs/AST_RELEVANCE.md`, `docs/AST_RELEVANCE_ACCEPTANCE.md`;
- Step 3.4: `docs/POSTGRESQL_PERSISTENCE.md` and PR #21;
- Step 3.5: `docs/REDIS_DRAMATIQ_WORKERS.md`, `docs/STEP_3_5_ACCEPTANCE.md`;
- Step 3.6: `docs/LEASE_HEARTBEAT.md`, `docs/STEP_3_6_ACCEPTANCE.md`;
- Step 3.7: `docs/RUN_TOKEN_FENCING.md`, `docs/STEP_3_7_ACCEPTANCE.md`.

---

## Step 3.1–3.6 frozen baseline

The accepted baseline entering Step 3.7 already guarantees:

1. deterministic project verification executes inside a bounded Docker sandbox;
2. `ContextPacketBuilder` owns readable scope, context budgets, provenance and truncation evidence;
3. AST/import relevance may select code regions but cannot widen TaskContract scope;
4. PostgreSQL stores typed/hash-validated runtime evidence while Git remains repository truth;
5. `(run_id, evidence_key)` remains the persistence idempotency boundary;
6. Redis/Dramatiq messages contain only `dispatch_id`, `run_id`, `task_id`;
7. workers reload Run/Task truth from PostgreSQL rather than trusting queue-carried task bodies;
8. queued success still requires validated runtime evidence plus actual Git evidence;
9. per-task PostgreSQL leases provide explicit ownership and heartbeat liveness;
10. PostgreSQL `clock_timestamp()` is authoritative for lease expiry;
11. Step 3.6 can classify an execution as EXPIRED/abandoned but deliberately cannot fence its later writes.

Frozen Step 3.6 principle:

> **Lease expiry can prove that ownership is no longer live from the coordinator's point of view; it cannot by itself prove that the old process has lost the technical ability to write.**

---

## Step 3.7 — `run_token` Stale-Write Protection — ACCEPTED

Merged through PR #24: `Phase 3 Step 3.7: add run token stale-write fencing`.

Squash merge commit:

`31a71bc45b58f8c865a54a972e96678e696f5c66`

Design / acceptance documents:

- `docs/RUN_TOKEN_FENCING.md`
- `docs/STEP_3_7_ACCEPTANCE.md`

### Accepted fencing architecture

```text
Redis / Dramatiq delivery
(dispatch_id, run_id, task_id only)
        ↓
PostgreSQL lease acquisition
        ↓
TaskLeaseGrant
  generation = N
  run_token = T_N
        ↓
LeasedQueuedTaskWorker
        ↓
current token + ACTIVE DB-time lease required
        ├── heartbeat / release
        ├── task-scoped PostgreSQL evidence
        ├── single-task finalization
        ├── generation worktree / branch creation
        └── final Git commit / ref publication

lease expires
        ↓
T_N loses write authority
        ↓
EXPIRED takeover with fresh dispatch
        ↓
generation = N + 1
run_token = fresh T_(N+1)
        ↓
T_N permanently stale
```

### Ownership-generation guarantees

- an UNOWNED task begins at generation `0` with no token;
- first acquisition issues generation `1` and a fresh UUID token;
- an ACTIVE generation remains exclusive;
- RELEASED ownership remains durable, non-reacquirable history;
- an EXPIRED generation may be atomically replaced with generation `N+1` and a fresh token;
- takeover requires a fresh `dispatch_id`, preventing a replacement generation from reusing the abandoned generation's dispatch-scoped evidence namespace;
- `worker_id` remains auditable owner metadata and is not the fencing credential.

`run_token` is returned directly from PostgreSQL acquisition to the worker process through `TaskLeaseGrant`. It is intentionally excluded from ordinary `TaskLeaseSnapshot` serialization/repr, queue messages, routine logs and branch names.

### PostgreSQL stale-write fencing

Token equality alone is insufficient. Worker-owned writes require both:

1. the exact current task `run_token`; and
2. an ACTIVE lease according to PostgreSQL database time.

This means lease expiry revokes the old generation's worker-write authority **before** another worker performs takeover.

Accepted fenced worker boundaries include:

- dispatch / worker execution evidence;
- runtime state-transition evidence;
- Developer, verification, Reviewer, Repair and failure evidence;
- leased-task context references;
- single-task terminal finalization;
- heartbeat and release.

Fencing occurs before evidence-key idempotency lookup, so a stale generation cannot make a late retry look accepted merely because an identical persistence key already exists.

The Step 3.4 local/non-leased persistence path remains available only when both persisted task state and caller are explicitly token-free. A caller-supplied fabricated token for an UNOWNED task fails closed rather than bypassing fencing through legacy behavior.

### Runtime-owned Git mutation fencing

Step 3.7 fences both Git mutation points owned by the queued runtime:

```text
git worktree add -b
        +
final task commit / branch ref publication
```

`PostgresTaskLeaseStore.guard_task_git_mutation()` locks the persisted Run/task, validates current token + live lease + dispatch, and keeps that lock across the bounded Git mutation.

This closes the ownership check/use race:

- if generation N enters the guard while live, that Git mutation linearizes before any later takeover;
- takeover cannot install generation N+1 in the middle of the protected mutation;
- if N is already expired or stale when it reaches the guard, the Git mutation is rejected.

A real PostgreSQL + real Git regression proves that after Worker B takeover, stale Worker A cannot even create a new runtime-owned branch ref; the Git ref set remains unchanged by A, while B's current generation can create its workspace and publish its result.

### Generation-scoped worktrees

Queued execution identity now incorporates generation identity derived from the token without exposing the token itself in the branch name.

This lets a valid replacement worker start from the persisted Run base even when an abandoned older generation left a registered/dirty worktree behind. Old-generation artifacts may remain inspectable evidence, but stale generations cannot create new runtime-owned refs or publish accepted result commits after losing the fence.

### Migration boundary

Migration:

`0003_run_token_fencing`

It adds `lease_generation` and `run_token` and tightens the task lease-shape constraint.

Existing Step 3.6-owned rows are upgraded to generation `1` with a database-generated fresh token. A pre-upgrade worker never received that token, so the migration does not create a compatibility path that lets the old process keep writing under the new fencing API.

### Final acceptance

Final exact-head CI for PR #24:

- PostgreSQL service: **PASS**;
- Redis service: **PASS**;
- Alembic `0001 → 0002 → 0003 → downgrade base → 0001 → 0002 → 0003`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- initial/concurrent lease-generation tests: **PASS**;
- expired-owner takeover + fresh-dispatch regression: **PASS**;
- stale heartbeat/evidence/finalization regressions: **PASS**;
- fabricated-token fail-closed regression: **PASS**;
- real stale Git-mutation fencing regression: **PASS**;
- real Redis/Dramatiq → worker-internal token propagation: **PASS**;
- `pytest`: **273 passed in 44.35s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- no paid SiliconFlow API call was required by Step 3.7 acceptance tests.

Step 3.7 does **not** claim exactly-once execution. A failed generation may have performed computation or left local artifacts before losing ownership. The accepted guarantee is narrower:

> **Only the current live execution generation may mutate DevFlow's accepted worker-owned PostgreSQL and runtime-owned Git boundaries.**

Step 3.7 also does not introduce an automatic worker-death redispatch/recovery controller.

Frozen Step 3.7 principle:

> **Lease expiry revokes the old generation's write authority; a fresh token fences the replacement generation so only the current live owner can mutate DevFlow worker state.**

---

## Gate before Step 3.8 — Structured run/event logs

Step 3.8 may now build structured observability on top of the accepted persistence, dispatch, ownership and fencing boundaries. It must observe existing truth rather than becoming a second source of lifecycle truth.

Required direction:

```text
validated runtime / worker / lease / fencing events
        ↓
structured event schema
        ↓
correlation by run / task / dispatch / generation / stage
        ↓
queryable chronological evidence
        ↓
future API / SSE / UI consumption
```

Step 3.8 should preserve:

- deterministic runtime state machines as lifecycle authority;
- PostgreSQL as durable evidence/ownership/fencing authority;
- Redis/Dramatiq as delivery transport;
- Git/worktree as code-state truth;
- current `run_token` as a write fence rather than a logging or success signal;
- explicit separation between operational telemetry and evidence that can authorize success.

Step 3.8 should focus on structured run/event observability and correlation. It must not use logging records to bypass deterministic verification, Reviewer decisions, Git evidence, lease ownership, or token fencing.

**Step 3.8 status: NOT STARTED.**
