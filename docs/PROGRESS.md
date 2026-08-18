# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions and failure history remain preserved in the corresponding pull requests, design documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.5 — Redis + Dramatiq Workers**
- Next item: **Step 3.6 — Lease + Heartbeat**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.6 status: **NOT STARTED**
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

Frozen V0.1 principle:

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

Step 2.7 squash merge commit:

`4d091949f1ba5a57465dc30edd4f5935f1a06fdd`

Phase 2 exit criterion is satisfied:

- validated dependency state and bounded parallel execution;
- isolated Git worktrees per task;
- successful task output frozen as auditable task commits;
- deterministic topological integration;
- structured merge-conflict evidence;
- durable Integration/Human Gate authorization;
- no LLM self-report can mark integration successful or bypass hard evidence gates.

**Phase 2 is frozen as ACCEPTED / COMPLETE.**

---

# Phase 3 — V0.3 Safety, Context and Reliability — IN PROGRESS

Current Phase 3 execution mapping:

1. **Step 3.1 — Docker Verification Sandbox + Resource Limits — ACCEPTED**
2. **Step 3.2 — Context Packet Builder — ACCEPTED**
3. **Step 3.3 — AST / Import-aware Relevant-code Extraction — ACCEPTED**
4. **Step 3.4 — PostgreSQL Persistence — ACCEPTED**
5. **Step 3.5 — Redis + Dramatiq Workers — ACCEPTED**
6. **Step 3.6 — Lease + Heartbeat — NEXT / NOT STARTED**
7. Step 3.7 — `run_token` stale-write protection
8. Step 3.8 — Structured run/event logs

---

## Step 3.1 — Docker Verification Sandbox + Resource Limits — ACCEPTED

Merged through PR #18.

Squash merge commit:

`7387e30e545ca05235dd40d06e6ab3fc15fcfbdd`

Accepted boundary:

- deterministic verification runs through `DockerSandboxRunner`;
- no silent host fallback for project-specific verification;
- task worktree and container root filesystem are read-only;
- no Docker socket mount;
- non-root user, dropped capabilities, no-new-privileges;
- network disabled;
- CPU, memory, PID, tmpfs and wall-clock bounds;
- immutable verification image identity;
- typed stdout/stderr/exit/duration and sandbox-policy evidence.

Final acceptance:

- verification image build: **PASS**;
- Ruff: **PASS**;
- pytest: **220 passed in 29.16s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**.

Frozen principle:

> Verification code may be untrusted; the runtime execution boundary must remain trusted and inspectable.

---

## Step 3.2 — Context Packet Builder — ACCEPTED

Merged through PR #19.

Squash merge commit:

`2a4d948b29045be3cbcf5ffae4f541d9135457bb`

Accepted boundary:

- runtime-owned bounded `ContextPacket` construction;
- managed-worktree path/source-size/UTF-8 checks;
- explicit file/character/token/source-size budgets;
- structured truncation and omission evidence;
- Git/path/scope provenance;
- canonical packet SHA-256 fingerprint;
- complete TaskContract projection validation;
- stage-local rebuild before Developer, every Repair, and Reviewer;
- Reviewer remains tool-less and receives actual Git diff + deterministic verification evidence.

Final acceptance:

- verification image build: **PASS**;
- Ruff: **PASS**;
- pytest: **228 passed in 27.17s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**.

Frozen principle:

> Relevance policy may evolve, but the ContextPacket protocol remains the authority for how model context is bounded, provenanced, truncated, fingerprinted, and rebuilt from current worktree truth.

---

## Step 3.3 — AST / Import-aware Relevant-code Extraction — ACCEPTED

Merged through PR #20.

Squash merge commit:

`02c6f1ecfe0398a339c34621443a1596b73a13de`

Design documents:

- `docs/AST_RELEVANCE.md`
- `docs/AST_RELEVANCE_ACCEPTANCE.md`

Accepted boundary:

- Python stdlib AST symbol extraction;
- deterministic task/path/symbol relevance;
- one-hop TaskContract-visible local import edges, including relative imports;
- imported-symbol definition selection;
- overlapping symbol-region de-duplication;
- ambiguous local imports fail closed rather than guess;
- invalid Python and non-Python text fall back to deterministic prefix selection;
- AST cannot widen TaskContract-readable scope or bypass ContextPacketBuilder budgets/trust checks;
- ContextPacket schema remains unchanged.

Final acceptance:

- verification image build: **PASS**;
- Ruff: **PASS**;
- pytest: **240 passed in 31.81s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**.

Frozen principle:

> **AST/import analysis may propose relevant code regions; ContextPacketBuilder still decides what can be read, how much can be included, and what auditable packet reaches an Agent.**

---

## Step 3.4 — PostgreSQL Persistence — ACCEPTED

Merged through PR #21: `Phase 3 Step 3.4: add PostgreSQL runtime evidence persistence`.

Squash merge commit:

`577da384072cc882c8851497351d99012de1f653`

Design document:

- `docs/POSTGRESQL_PERSISTENCE.md`

### Accepted persistence architecture

```text
Git / Commit / Worktree
        +
Validated runtime domain evidence
        ↓
PostgresEvidenceStore
        ├── projects
        ├── runs
        ├── tasks
        └── evidence_records
                ├── typed evidence kind
                ├── schema version
                ├── JSONB payload
                └── canonical SHA-256
        ↓
PostgreSQL
        ↓
Durable recovery / query / audit
```

### Source-of-truth boundary

PostgreSQL is a durability layer, not a replacement for Git or deterministic runtime evidence.

- Git commit/worktree remains authoritative for repository contents and code state;
- persisted LLM text cannot become a success signal;
- there is no generic database API that can simply set a run to `SUCCEEDED`;
- single-task terminal persistence accepts a schema-valid terminal `SingleTaskRunResult` and verifies persisted status against that result during read-back;
- a partial persisted `RUNNING` run means committed evidence survived, not that execution automatically resumed or that a terminal outcome can be inferred.

### Relational and typed evidence boundary

Step 3.4 accepts four core tables:

- `projects`: stable repository identity;
- `runs`: execution identity, base Git commit, coarse persistence lifecycle, optional terminal runtime result;
- `tasks`: immutable per-run `TaskContract` payload + canonical hash;
- `evidence_records`: append-oriented task-level or run-level typed evidence.

Accepted evidence kinds cover existing structured runtime models for:

- state transitions;
- Developer runs;
- deterministic verification;
- Reviewer decisions;
- Repair attempts;
- failures;
- merge queue evidence;
- merge conflicts;
- Integration/Human Gate evidence;
- Context fingerprint references.

`ContextFingerprintReference` persists packet identity, repository HEAD, strategy names, changed-file names and aggregate usage, but intentionally does not copy `ContextSnippet.content` or repository file bodies into PostgreSQL.

### Transaction and concurrency semantics

- `start_run()` atomically creates a `RUNNING` run plus one or more immutable TaskContract rows;
- `(run_id, evidence_key)` is the persistence idempotency boundary;
- identical evidence retry returns the existing evidence id;
- conflicting reuse of the same key fails closed;
- evidence append and terminal finalization lock the run row, serializing same-run persistence decisions;
- concurrent identical appends from independent async stores resolve to one evidence row / one evidence id;
- terminal runs are append-closed;
- TaskContract, evidence and terminal payloads are canonical-hashed and typed-validated during read-back;
- detached payload tampering is reported as persistence corruption rather than trusted state.

These guarantees are database-transaction guarantees only. They do not implement distributed worker lease ownership or stale-writer fencing.

### Migration boundary

Alembic owns schema evolution. CI validates the initial PostgreSQL migration using:

```text
upgrade head
    ↓
downgrade base
    ↓
upgrade head
```

Runtime code does not use `metadata.create_all()` as an implicit production migration path.

### Step 3.4 acceptance history

- First candidate: real PostgreSQL migration + Docker image passed; Ruff stopped on **9 static formatting/import issues**. Fixed without changing persistence semantics.
- Second candidate: PostgreSQL migration + Docker + Ruff passed; pytest reached **247 passed / 0 skipped**.
- Merge review identified a same-run concurrency race between evidence append/finalization and concurrent identical idempotency writes.
- Final hardening serialized same-run writes using run-row locking and added a real PostgreSQL concurrency regression test.

Final acceptance:

- PostgreSQL migration upgrade/downgrade/upgrade: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- `pytest`: **248 passed in 27.90s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- all previously accepted V0.1, Phase 2 and Step 3.1–3.3 behavior remained green.

### Step 3.4 frozen boundary

Step 3.4 intentionally does **not** introduce:

- Redis or Dramatiq;
- distributed dispatch/worker ownership;
- lease/heartbeat;
- `run_token` stale-write protection;
- automatic restart/resume policy;
- full structured run/event streaming;
- frontend behavior;
- embeddings/vector retrieval;
- additional AST/RAG/Agent behavior;
- automatic LLM merge-conflict repair.

Frozen Step 3.4 principle:

> **PostgreSQL can preserve, validate, query, and replay evidence; only the existing deterministic runtime and Git evidence are allowed to decide what that evidence means.**

---

## Step 3.5 — Redis + Dramatiq Workers — ACCEPTED

Merged through PR #22: `Phase 3 Step 3.5: add Redis Dramatiq queued workers`.

Squash merge commit:

`51048e4818478c07e2c024d78215a43959a56465`

Design / acceptance documents:

- `docs/REDIS_DRAMATIQ_WORKERS.md`
- `docs/STEP_3_5_ACCEPTANCE.md`

### Accepted queued-worker architecture

```text
Validated persisted Run / Task
        ↓
DramatiqTaskDispatcher
        ↓
TaskDispatchEnvelope
(dispatch_id, run_id, task_id)
        ↓
Redis
        ↓
Dramatiq actor
        ↓
QueuedTaskWorker
        ↓
PostgreSQL Run/Task reload + validation
        ↓
managed Git repository
        ↓
run-scoped isolated task worktree
        ↓
existing SingleTaskOrchestrator
        ↓
validated runtime result
        +
actual Git task commit
        ↓
typed PostgreSQL dispatch/runtime/worker evidence
```

### Dispatch and trust boundary

- Redis messages contain only `dispatch_id`, `run_id`, and `task_id`;
- TaskContract bodies, repository source, workspace paths from callers, prompts, model output and credentials do not enter queue payloads;
- dispatcher performs a fresh persistence read before enqueue and rejects terminal runs or tasks not bound to the persisted run;
- worker reloads persisted Run/Task identity before execution instead of trusting queue-carried task content;
- Redis/Dramatiq is transport, not the DAG scheduler, execution owner, code source of truth, or completion signal;
- PostgreSQL remains durable runtime evidence;
- Git commit/worktree remains authoritative for code state.

### Worker execution boundary

- production worker reuses the accepted `SingleTaskOrchestrator`, Developer/Reviewer/Repair, Docker verifier and worktree machinery;
- no second execution state machine or queue-owned scheduler was introduced;
- runtime success alone is insufficient for queued success;
- successful queued execution requires an actual Git task commit after the validated runtime result;
- runtime success followed by Git task-commit failure remains `WORKER_EXECUTION=FAILED` and cannot finalize the persisted run as `SUCCEEDED`;
- state/developer/verification/review/repair/failure evidence is persisted under dispatch-scoped evidence keys;
- `DISPATCH_EVENT` and `WORKER_EXECUTION` use the accepted Step 3.4 typed JSONB/version/hash boundary, so Step 3.5 required no Alembic migration;
- single-task persisted runs may finalize only when worker execution status agrees with the nested terminal `SingleTaskRunResult`;
- multi-task project-run finalization remains an orchestration/integration concern.

### Retry and redelivery boundary

The DevFlow Dramatiq actor explicitly uses:

```text
max_retries = 0
```

Step 3.5 does not claim exactly-once execution. Without lease/heartbeat and `run_token` fencing, repository-mutating application retries are not treated as safe ownership recovery.

Current fail-closed behavior includes:

- terminal persisted runs reject later execution;
- conflicting persistence evidence keys are rejected;
- same-run duplicate task execution collides on the same run-scoped worktree/branch identity rather than silently reusing it.

### Git reproducibility hardening

Merge review identified two process-external execution issues and hardened both before acceptance.

1. **Historical task branch collision across runs**
   - Phase 2 task branches intentionally survive worktree removal as evidence;
   - queued worktree identity is now scoped by `run_id` plus logical `task_id`;
   - duplicate delivery within the same run still collides fail-closed;
   - a later independent run may reuse the same logical task id with a distinct retained Git identity.

2. **Persisted Run base older than managed HEAD**
   - a task may wait in Redis while the managed repository advances;
   - queued execution freezes the persisted Run `base_commit` instead of silently replacing it with current HEAD;
   - the commit must still exist and remain an ancestor of the managed HEAD;
   - divergence/history replacement fails closed;
   - accepted regression evidence proves the resulting task commit descends directly from the persisted Run base and excludes repository changes committed after the Run started.

The new `TaskWorktreeManager(frozen_base_commit=...)` path is backward compatible: existing Phase 2 callers retain the prior current-HEAD freeze behavior unless they explicitly provide a persisted frozen base.

### Step 3.5 acceptance history

- First candidate: PostgreSQL + Redis services, Alembic migration cycle, Docker image and Ruff passed; pytest reached **254 passed / 2 failed**.
- Both first-candidate failures were test-assertion mismatches with actual Dramatiq/test evidence APIs; production Redis → Dramatiq Worker → PostgreSQL integration was already passing.
- Assertion-only repair candidate reached **256 passed / 0 skipped**.
- Merge review then found the cross-run branch-identity and delayed-base reproducibility issues described above.
- Final hardening added run-scoped worktree identity, persisted frozen-base validation and dedicated real-Git regressions.

Final acceptance:

- PostgreSQL service: **PASS**;
- Redis service: **PASS**;
- PostgreSQL migration upgrade/downgrade/upgrade: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- real Redis + Dramatiq Worker + PostgreSQL integration: **PASS**;
- `pytest`: **258 passed in 28.48s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- no paid SiliconFlow API call was used by queue integration tests;
- all previously accepted V0.1, Phase 2 and Step 3.1–3.4 behavior remained green.

### Step 3.5 frozen boundary

Step 3.5 intentionally does **not** introduce:

- lease expiry;
- heartbeat renewal;
- `run_token` stale-write protection;
- stale-worker fencing;
- automatic worker-death recovery;
- exactly-once execution claims;
- new DAG scheduler semantics;
- frontend/SSE behavior;
- embeddings/vector retrieval;
- additional AST/RAG/Agent behavior;
- automatic LLM merge-conflict repair;
- a new PostgreSQL schema migration.

Frozen Step 3.5 principle:

> **Queue delivery authorizes an execution attempt; Git and validated runtime evidence still decide what actually happened.**

---

## Gate before Step 3.6 — Lease + Heartbeat

Step 3.6 may add explicit worker-execution ownership and liveness evidence around the accepted queued-worker boundary. It must not yet implement Step 3.7 `run_token` stale-write fencing or reinterpret queue delivery as success.

Required direction:

```text
Persisted RUNNING task
        ↓
queued worker receives attempt
        ↓
lease acquisition / ownership record
        ↓
periodic heartbeat renewal
        ↓
lease remains live
        or
lease expiry exposes abandoned execution
        ↓
existing runtime / Git / PostgreSQL evidence
```

Step 3.6 must preserve:

- Redis/Dramatiq as dispatch transport rather than task-success authority;
- Git/worktree as repository source of truth;
- PostgreSQL as durable runtime/ownership evidence;
- deterministic runtime gates and existing TaskContract scope;
- bounded lease duration and heartbeat cadence;
- explicit, testable ownership transitions rather than inference from worker prose;
- the Step 3.5 rule that a task is not successful without validated runtime evidence and Git evidence.

Step 3.6 must **not** yet introduce:

- `run_token` stale-write fencing;
- claims that an expired old worker is technically unable to write after ownership transfer;
- frontend behavior;
- embeddings/vector retrieval;
- Agent/AST/RAG expansion.

**Step 3.6 status: NOT STARTED.**
