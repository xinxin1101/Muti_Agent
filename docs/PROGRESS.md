# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions and failure history remain preserved in the corresponding pull requests, design documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.4 — PostgreSQL Persistence**
- Next item: **Step 3.5 — Redis + Dramatiq Workers**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.5 status: **NOT STARTED**
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
5. **Step 3.5 — Redis + Dramatiq Workers — NEXT / NOT STARTED**
6. Step 3.6 — Lease + heartbeat
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

## Gate before Step 3.5 — Redis + Dramatiq Workers

Step 3.5 may move task execution from in-process worker calls to a durable queue/worker boundary. It must build on the accepted PostgreSQL evidence schema rather than replacing it, and it must not prematurely implement lease/heartbeat or `run_token` fencing.

Required direction:

```text
Validated Task / Run identity
        ↓
Dispatch boundary
        ↓
Redis-backed Dramatiq queue
        ↓
Bounded worker consumer
        ↓
Existing runtime execution
        ↓
PostgreSQL evidence persistence
```

Step 3.5 must preserve:

- Git/worktree as repository source of truth;
- PostgreSQL as durable evidence rather than queue authority;
- deterministic runtime gates and TaskContract scope;
- existing bounded parallelism and isolated worktree semantics;
- explicit queue message schema and idempotent persistence writes;
- no LLM self-report as completion signal;
- no silent fallback to in-process execution when the distributed worker boundary is configured.

Step 3.5 must **not** yet introduce:

- lease expiry/renewal semantics;
- heartbeat ownership;
- `run_token` stale-write fencing;
- frontend behavior;
- embeddings/vector retrieval;
- Agent/AST/RAG expansion.

**Step 3.5 status: NOT STARTED.**
