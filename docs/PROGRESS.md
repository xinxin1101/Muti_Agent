# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions, failure history, and merge-review hardening remain preserved in the corresponding pull requests, acceptance documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.6 — Lease + Heartbeat**
- Next item: **Step 3.7 — `run_token` stale-write protection**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.7 status: **NOT STARTED**
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

V0.1 exit criterion is satisfied: a validated `TaskContract` can run through Developer → Git scope → deterministic verification → independent semantic review → targeted repair → terminal evidence without trusting an LLM self-report as the success signal.

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

Phase 2 squash completion commit:

`4d091949f1ba5a57465dc30edd4f5935f1a06fdd`

Accepted boundary:

- validated dependency truth and bounded parallel execution;
- isolated Git worktrees per task;
- successful task output frozen as auditable task commits;
- deterministic topological integration;
- structured merge-conflict evidence;
- evidence-bound Integration/Human Gate authorization;
- no LLM self-report can mark integration successful or bypass hard evidence gates.

**Phase 2 is frozen as ACCEPTED / COMPLETE.**

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
| 3.7 | `run_token` stale-write protection | **NEXT / NOT STARTED** | — | — |
| 3.8 | Structured run/event logs | NOT STARTED | — | — |

Detailed accepted design/acceptance documents:

- Step 3.1: sandbox implementation and tests in PR #18;
- Step 3.2: Context Packet implementation and tests in PR #19;
- Step 3.3: `docs/AST_RELEVANCE.md`, `docs/AST_RELEVANCE_ACCEPTANCE.md`;
- Step 3.4: `docs/POSTGRESQL_PERSISTENCE.md` and PR #21;
- Step 3.5: `docs/REDIS_DRAMATIQ_WORKERS.md`, `docs/STEP_3_5_ACCEPTANCE.md`;
- Step 3.6: `docs/LEASE_HEARTBEAT.md`, `docs/STEP_3_6_ACCEPTANCE.md`.

---

## Step 3.1–3.5 frozen baseline

The accepted baseline entering Step 3.6 already guarantees:

1. **Sandboxed deterministic verification**
   - project verification executes in a bounded Docker sandbox;
   - no silent host fallback;
   - read-only project/worktree mount, no Docker socket, no network, bounded CPU/memory/PIDs/time.

2. **Bounded, provenance-aware model context**
   - `ContextPacketBuilder` owns readable scope, budgets, truncation evidence and packet fingerprint;
   - AST/import analysis may rank/select relevant regions but cannot widen TaskContract scope.

3. **Durable PostgreSQL runtime evidence**
   - Git remains authoritative for repository contents;
   - PostgreSQL stores typed/hash-validated runtime evidence;
   - `(run_id, evidence_key)` is the idempotency boundary;
   - same-run writes are transactionally serialized;
   - terminal Runs are append-closed.

4. **Process-external Redis/Dramatiq execution**
   - queue payload contains only `dispatch_id`, `run_id`, `task_id`;
   - worker reloads Run/Task truth from PostgreSQL;
   - Redis is transport, not scheduler/ownership/success authority;
   - `max_retries=0` remains deliberate before stale-write fencing;
   - queued success requires validated runtime evidence plus an actual Git task commit;
   - queued worktrees are run-scoped and execute from the persisted immutable Run base.

Frozen Step 3.5 principle:

> **Queue delivery authorizes an execution attempt; Git and validated runtime evidence still decide what actually happened.**

---

## Step 3.6 — Lease + Heartbeat — ACCEPTED

Merged through PR #23: `Phase 3 Step 3.6: add task lease and heartbeat ownership`.

Squash merge commit:

`c7732d3ad832ff4abd159b322c8f39381159ed83`

Design / acceptance documents:

- `docs/LEASE_HEARTBEAT.md`
- `docs/STEP_3_6_ACCEPTANCE.md`

### Accepted ownership architecture

```text
Redis / Dramatiq delivery
        ↓
LeasedQueuedTaskWorker
        ↓
PostgreSQL task lease acquisition
        ↓
ACTIVE owner
        ↓
periodic heartbeat renewal
        ↓
existing QueuedTaskWorker
        ↓
existing Git / runtime / verification / review / repair
        ↓
normal completion
        ↓
RELEASED

heartbeat stops / renewal fails
        ↓
lease_until passes
        ↓
EXPIRED
        ↓
abandoned-execution evidence only
```

Lease ownership is per `(run_id, task_id)`, not per whole Run, so independent DAG tasks may be owned by different workers concurrently.

Persisted task lease fields are:

```text
lease_owner
lease_dispatch_id
lease_acquired_at
heartbeat_at
lease_until
lease_released_at
```

Migration:

`0002_task_lease_heartbeat`

No `run_token` or equivalent fencing generation exists in Step 3.6.

### Lease state semantics

```text
UNOWNED
   ↓ acquire
ACTIVE
   ├── exact-owner heartbeat → ACTIVE with later lease_until
   ├── live completion/failure unwind → RELEASED
   └── no heartbeat before deadline → EXPIRED
```

- **UNOWNED**: no execution owner has ever acquired this task lease.
- **ACTIVE**: exact `(worker_id, dispatch_id)` owns a lease whose deadline is after PostgreSQL observation time.
- **RELEASED**: the exact owner explicitly released a still-live lease; history remains durable and is not silently reused.
- **EXPIRED**: latest deadline has passed without release; the coordinator may classify that execution as abandoned.

PostgreSQL `clock_timestamp()` is authoritative for acquisition, heartbeat, deadline comparison, release, and inspection. Worker-local clocks do not decide liveness.

### Acquisition and concurrency guarantees

- new lease acquisition requires the persisted Run to be `RUNNING`;
- Run/task rows are locked inside the lease transaction;
- any existing lease history is non-reacquirable in Step 3.6;
- two independent PostgreSQL stores racing for the same `(run_id, task_id)` are proven to yield exactly one ACTIVE owner and one fail-closed conflict;
- wrong worker or dispatch identity cannot renew or release another owner's lease.

### Heartbeat and terminal unwind

`LeasedQueuedTaskWorker` wraps the accepted Step 3.5 worker rather than replacing it.

- heartbeat interval must be shorter than lease duration;
- heartbeat renews both `heartbeat_at` and `lease_until`;
- heartbeat failure cooperatively cancels the inner asyncio execution and leaves ownership to become EXPIRED;
- cooperative cancellation is explicitly **not** stale-write fencing;
- existing deterministic verification and ContextPacket construction already leave the event loop via `asyncio.to_thread`, so long Docker verification does not intentionally starve heartbeat scheduling.

A merge-review race was hardened before acceptance: a single-task inner worker may finalize its Run immediately before the outer lease wrapper stops heartbeat/releases ownership. Therefore:

- terminal Run → **new acquire is always rejected**;
- exact already-established still-live owner → may heartbeat/release during deterministic terminal unwind;
- expired/released lease → cannot renew.

This keeps successful terminal persistence from being falsely converted into a heartbeat failure during cleanup.

### Worker identity

`DEVFLOW_WORKER_ID` can provide an explicit owner label.

Without explicit configuration, the fallback identity is generated and cached by actual process PID:

```text
hostname : pid : random-process-suffix
```

It is stable within one worker process and changes after a fork/process change. Worker identity is auditable metadata, not a credential or fencing token.

### Explicit Step 3.7 boundary

Step 3.6 intentionally proves that liveness detection is **not** write fencing.

A real PostgreSQL regression demonstrates:

```text
Worker A lease expires
        ↓
TaskLeaseSnapshot = EXPIRED
        ↓
old caller can still reach existing PostgresEvidenceStore.append_evidence()
while Run remains RUNNING
```

Therefore Step 3.6 deliberately does **not** reassign an EXPIRED task to Worker B.

`EXPIRED` means:

> the recorded ownership is no longer live from the coordinator's point of view.

It does **not** mean:

> the old process has technically lost the ability to write Git/PostgreSQL state.

### Final acceptance

Final exact-head CI for PR #23:

- PostgreSQL service: **PASS**;
- Redis service: **PASS**;
- Alembic `0001 → 0002 → downgrade base → 0001 → 0002`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- real PostgreSQL concurrent lease acquisition: **PASS**;
- terminal-run lease unwind regression: **PASS**;
- Redis/Dramatiq worker regressions: **PASS**;
- explicit stale-write-still-possible Step 3.7 boundary regression: **PASS**;
- `pytest`: **270 passed in 29.08s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- no paid SiliconFlow API call was required by lease/heartbeat tests.

Frozen Step 3.6 principle:

> **Lease expiry can prove that ownership is no longer live from the coordinator's point of view; it cannot prove that the old process has lost the technical ability to write.**

---

## Gate before Step 3.7 — `run_token` stale-write protection

Step 3.7 must close the safety gap that Step 3.6 deliberately exposes.

Target transition:

```text
Step 3.6
lease_owner + heartbeat + lease_until
        ↓
can identify abandoned ownership
        ↓
BUT old worker may still write

Step 3.7
fresh run_token / ownership generation
        ↓
all mutable worker write boundaries require current token
        ↓
expired old owner keeps stale token
        ↓
stale writes fail closed
        ↓
only then can ownership transfer / recovery become safe
```

Required Step 3.7 direction:

1. **Fresh ownership generation**
   - acquisition/takeover must issue a fresh unpredictable or monotonic fencing identity (`run_token`);
   - ownership identity and fencing generation must not be conflated with human-readable `worker_id`.

2. **Fence actual mutable boundaries**
   - PostgreSQL worker-owned evidence writes/finalization must require the current token;
   - lease heartbeat/release must be tied to the current token;
   - Git/worktree finalization paths that can publish worker output must verify current ownership before accepting the write;
   - a stale worker must not be able to turn late execution into accepted evidence merely because it still has process access.

3. **Safe expired-owner transfer**
   - only after stale writes are fenced may an EXPIRED lease be replaced by a new owner/token;
   - old token remains permanently stale after transfer;
   - Worker A cannot regain authority by sending a later heartbeat with its stale token.

4. **Preserve existing truth boundaries**
   - Redis remains transport, not ownership/success authority;
   - Git remains repository/code truth;
   - PostgreSQL remains durable runtime/ownership evidence;
   - deterministic verifier + Reviewer + Git commit evidence remain required for success;
   - `run_token` is an authorization/fencing primitive, never a task-success signal.

Step 3.7 must include real concurrency regressions for at least:

- Worker A ACTIVE → lease expires → Worker B takeover receives fresh token;
- Worker A late heartbeat rejected after takeover;
- Worker A late PostgreSQL evidence append rejected;
- Worker A late terminal/finalization write rejected;
- Worker B current-token writes accepted;
- duplicate/current-token idempotent behavior remains deterministic;
- no regression to Step 3.4 persistence idempotency, Step 3.5 queue boundary, or Step 3.6 heartbeat behavior.

Step 3.7 must **not** yet introduce:

- frontend/SSE behavior;
- embeddings/vector retrieval;
- new Agent/AST/RAG behavior;
- generic automatic LLM merge-conflict repair;
- broad structured observability work reserved for Step 3.8.

**Step 3.7 status: NOT STARTED.**
