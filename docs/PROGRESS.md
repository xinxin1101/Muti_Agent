# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this ledger records *what has actually passed acceptance*. Detailed implementation discussions and failure history remain preserved in the corresponding pull requests, design documents, CI runs, and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.3 — AST / Import-aware Relevant-code Extraction**
- Next item: **Step 3.4 — PostgreSQL Persistence**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.4 status: **NOT STARTED**
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

V0.1 exit criterion is satisfied: one validated `TaskContract` can run through Developer → Git scope → deterministic verification → independent semantic review → targeted repair → terminal `SUCCEEDED` / `FAILED` evidence without trusting an LLM self-report as the success signal.

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
- one isolated Git worktree per task;
- successful task output frozen as auditable task commits;
- deterministic topological integration;
- structured merge-conflict evidence;
- durable policy + Human Gate authorization;
- no LLM self-report can mark integration successful or bypass hard evidence gates.

**Phase 2 is frozen as ACCEPTED / COMPLETE.**

---

# Phase 3 — V0.3 Safety, Context and Reliability — IN PROGRESS

Current Phase 3 execution mapping:

1. **Step 3.1 — Docker Verification Sandbox + Resource Limits — ACCEPTED**
2. **Step 3.2 — Context Packet Builder — ACCEPTED**
3. **Step 3.3 — AST / Import-aware Relevant-code Extraction — ACCEPTED**
4. **Step 3.4 — PostgreSQL Persistence — NEXT / NOT STARTED**
5. Redis + Dramatiq workers
6. Lease + heartbeat
7. `run_token` stale-write protection
8. Structured run/event logs

---

## Step 3.1 — Docker Verification Sandbox + Resource Limits — ACCEPTED

Merged through PR #18.

Squash merge commit:

`7387e30e545ca05235dd40d06e6ab3fc15fcfbdd`

Accepted boundary:

- production deterministic verification runs through `DockerSandboxRunner`;
- no silent host fallback for project-specific verification;
- task worktree and container root filesystem are read-only;
- no Docker socket mount;
- non-root user, `--cap-drop ALL`, `no-new-privileges=true`;
- `--network none`;
- CPU, memory, memory-swap, PID, `/dev/shm`, tmpfs, and wall-clock bounds;
- unique container name + forced cleanup on timeout;
- verification image resolved to immutable image ID before execution;
- `--pull never` and cleared image ENTRYPOINT;
- stdout/stderr/exit/duration plus sandbox policy are preserved as verification evidence;
- pytest, Ruff, sandbox timeout, and infrastructure failures retain typed failure classification.

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

Accepted chain:

```text
TaskContract
      +
Trusted managed worktree state
      ↓
ContextPacketBuilder
      ├── objective / acceptance criteria / scopes
      ├── selected files and snippets
      ├── Git/path/scope provenance
      ├── explicit file/char/token/source-size budgets
      ├── truncation / omission evidence
      └── canonical SHA-256 fingerprint
      ↓
ContextPacket
      ↓
Developer / Repair / Reviewer
```

Accepted boundary:

- repository text in a packet is untrusted model data;
- runtime Task/Git/scope/budget/fingerprint metadata is control-plane evidence;
- source inspection remains bounded by managed-worktree path checks, regular-file checks, source-size limit, and UTF-8 validation;
- provider-neutral token budget is explicitly `utf8_bytes_upper_bound`, not claimed as an exact model tokenizer;
- existing truncation taxonomy records per-file, total-char, token, file-count, source-size, non-UTF8, and unavailable-path evidence;
- packet payload is canonically fingerprinted and Pydantic validation rejects forged/detached fingerprints;
- Developer, Repair, and Reviewer verify the complete TaskContract projection carried by the packet;
- production Orchestrator rebuilds the packet immediately before Developer, each Repair, and Reviewer after hard verification, so repository truth remains stage-local;
- Reviewer remains read-only and still receives actual Git diff + deterministic verification evidence.

Final acceptance:

- verification image build: **PASS**;
- Ruff: **PASS**;
- pytest: **228 passed in 27.17s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**.

Frozen Step 3.2 principle:

> Later relevance improvements may change **what code is selected**, but must not bypass or silently redefine how context is bounded, provenanced, truncated, fingerprinted, or rebuilt from current worktree truth.

---

## Step 3.3 — AST / Import-aware Relevant-code Extraction — ACCEPTED

Merged through PR #20: `Phase 3 Step 3.3: add AST import-aware relevance selection`.

Squash merge commit:

`02c6f1ecfe0398a339c34621443a1596b73a13de`

Design documents:

- `docs/AST_RELEVANCE.md`
- `docs/AST_RELEVANCE_ACCEPTANCE.md`

### Accepted selection architecture

```text
TaskContract
      +
Task-visible current worktree candidates
      ↓
RelevantCodeExtractor
      ├── Python AST symbols
      ├── deterministic task/path lexical terms
      ├── visible one-hop local imports
      ├── relative-import resolution
      ├── imported-symbol definition relevance
      ├── bounded region selection
      └── deterministic internal relevance evidence
      ↓
RelevantFileSelection / RelevantCodeRegion
      ↓
ContextPacketBuilder
      ├── existing trusted source-read boundary
      ├── existing ContextBudget
      ├── existing truncation taxonomy
      ├── existing Git/path/scope provenance
      └── existing canonical fingerprint
      ↓
ContextPacket
```

### Protocol boundary preserved

Step 3.3 does **not** modify `backend/app/models/context.py` and does not add fields to:

- `ContextPacket`;
- `ContextFile`;
- `ContextSnippet`.

AST/import relevance is an internal selection layer. The accepted Step 3.2 Builder remains authoritative for trusted reads, byte/UTF-8/source-size checks, packet budgets, truncation evidence, source SHA-256, provenance, and canonical packet integrity.

### Python-aware relevance policy

The first accepted language-aware implementation intentionally supports Python through stdlib `ast`:

- Python functions, classes, and class methods are indexed as bounded symbols;
- task objective, acceptance criteria, and task scopes produce deterministic lexical terms;
- changed/writable/read-only/readable facts remain strong file-ranking signals;
- symbol names require actual task-term or imported-symbol overlap before structural weighting can increase their score;
- unrelated top-level definitions are not selected merely because they are top-level;
- changed/writable Python files retain a deterministic top-level fallback when no lexical symbol matches;
- overlapping class/method regions are de-duplicated so the stronger region wins;
- module docstring/import preamble is retained when useful for selected definitions;
- invalid Python syntax falls back to the Step 3.2 deterministic-prefix path;
- non-Python text also uses deterministic-prefix fallback.

This step does not claim multi-language AST support.

### Local import boundary

Local dependency edges are built only from TaskContract-visible Python candidate paths.

Accepted behavior includes:

- `import package.module`;
- `from package.module import Symbol`;
- relative imports such as `from .models import User`;
- one-hop visible dependency ranking;
- imported symbol names used to prioritize the corresponding definition region when resolvable.

Import resolution prefers the most-specific visible alias. If an alias maps to multiple visible repository paths, DevFlow **fails closed for that edge** instead of selecting a lexicographic path. Determinism is not used as justification for guessing.

Imports cannot widen TaskContract-readable scope.

### Bounded selector execution

The selector itself has independent deterministic bounds for:

- initially indexed Python files;
- additional one-hop dependency files;
- selected symbol regions per file.

These execution bounds do not replace `ContextBudget`. AST-selected snippets still consume the existing file/character/token budgets and reuse the existing truncation taxonomy.

The selector has no network access path, LLM call, embedding/vector service, random selection, repository mutation capability, or dynamic code execution.

### Step 3.3 acceptance history

- First candidate: verification image build passed; Ruff stopped on **3 static issues** (2 line-length + 1 SIM102). Fixed without behavior changes.
- Second candidate: Docker image + Ruff passed; pytest reached **236 passed / 3 failed**. The failures exposed real selector defects:
  - top-level bonus could create false relevance for unrelated symbols;
  - overlapping class/method regions produced redundant provenance.
- Merge review additionally identified ambiguous local-module suffix resolution as deterministic-but-unsafe guessing.
- Final hardening:
  - requires real task/import overlap before top-level weighting;
  - removes overlapping symbol regions deterministically;
  - rejects ambiguous local-import edges rather than guessing.

Final acceptance:

- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- `pytest`: **240 passed in 31.81s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- all previously accepted V0.1, Phase 2, Step 3.1, and Step 3.2 behavior remained green.

### Step 3.3 frozen boundary

Step 3.3 intentionally does **not** introduce:

- changes to the ContextPacket schema;
- embeddings or vector retrieval;
- LLM-based code selection;
- call-graph or dynamic execution analysis;
- PostgreSQL;
- Redis/Dramatiq;
- distributed lease/heartbeat/run-token behavior;
- frontend behavior;
- changes to Docker verification, Reviewer/Repair semantics, DAG/scheduler/worktree/merge/conflict/Human Gate behavior.

Frozen Step 3.3 principle:

> **AST/import analysis may propose relevant code regions; the existing ContextPacketBuilder still decides what can be read, how much can be included, and what auditable packet reaches an Agent.**

---

## Gate before Step 3.4 — PostgreSQL Persistence

Step 3.4 may make accepted runtime evidence durable across process restarts. It must not redefine Agent success, verification semantics, ContextPacket selection, or Git/worktree truth.

Required direction:

```text
Existing runtime evidence
      ├── Project / Run / Task identity
      ├── AgentRun evidence
      ├── VerificationResult / CheckResult
      ├── Review / Repair evidence
      ├── Task state transitions
      ├── merge / conflict / Human Gate evidence
      └── context identity / fingerprint references where appropriate
      ↓
Persistence boundary
      ↓
PostgreSQL
      ├── explicit relational schema
      ├── SQLAlchemy 2 models / repositories
      ├── migrations / initialization strategy
      ├── transaction boundaries
      ├── typed serialization of structured evidence
      └── deterministic read-back tests
```

Step 3.4 must preserve these principles:

- PostgreSQL is a **durability layer**, not the source of truth for repository contents;
- Git commit/worktree evidence remains authoritative for code state;
- LLM text cannot become a success signal merely because it was persisted;
- runtime state transitions must not be silently reconstructed from prose;
- writes must have explicit transaction/error behavior;
- secrets must not be persisted in run evidence;
- schema choices should leave room for the later Redis/Dramatiq + lease/heartbeat/run-token worker model without implementing those features early.

Step 3.4 must not introduce Redis/Dramatiq, lease/heartbeat, `run_token`, frontend behavior, embeddings/vector retrieval, or automatic LLM conflict repair.

**Step 3.4 status: NOT STARTED.**
