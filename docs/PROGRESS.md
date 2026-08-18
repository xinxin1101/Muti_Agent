# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*. Detailed implementation discussions remain preserved in the corresponding pull requests and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 3.2 — Context Packet Builder**
- Next item: **Step 3.3 — AST / Import-aware Relevant-code Extraction**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **IN PROGRESS**
- Step 3.3 status: **NOT STARTED**
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

Phase 2 exit criterion is satisfied:

- validated dependency state and bounded parallel execution;
- one isolated Git worktree per task;
- successful task output frozen as auditable task commits;
- deterministic topological object-level integration;
- recoverable merge-conflict markers;
- structured, replayable conflict evidence;
- durable policy + Human Gate authorization that cannot bypass hard Repair boundaries;
- no LLM self-report can mark integration successful or bypass Git evidence.

Step 2.7 squash merge commit:

`4d091949f1ba5a57465dc30edd4f5935f1a06fdd`

**Phase 2 is frozen as ACCEPTED / COMPLETE.**

---

# Phase 3 — V0.3 Safety, Context and Reliability — IN PROGRESS

`docs/DEVELOPMENT_PLAN.md` originally lists Docker sandboxing and CPU/memory/network/time limits as adjacent Phase 3 roadmap items. The Step 3.1 execution gate intentionally accepted them together because resource isolation is part of the sandbox trust boundary rather than a useful standalone follow-up.

Current Phase 3 execution mapping:

1. **Step 3.1 — Docker Verification Sandbox + resource limits — ACCEPTED**
2. **Step 3.2 — Context Packet Builder — ACCEPTED**
3. **Step 3.3 — AST / Import-aware Relevant-code Extraction — NEXT / NOT STARTED**
4. PostgreSQL persistence
5. Redis + Dramatiq workers
6. Lease + heartbeat
7. `run_token` stale-write protection
8. Structured run/event logs

## Step 3.1 — Docker Verification Sandbox + Resource Limits — ACCEPTED

Merged through PR #18: `Phase 3 Step 3.1: add Docker verification sandbox`.

Squash merge commit:

`7387e30e545ca05235dd40d06e6ab3fc15fcfbdd`

### Production execution boundary

- `DeterministicVerifier` still owns scope-first gating and typed verification semantics, but command execution now goes through a `VerificationCommandRunner` boundary.
- Production/default composition uses `DockerSandboxRunner`.
- The explicit host runner remains only for focused unit tests/migration checks and refuses project-specific custom commands.
- Project-specific verification commands therefore become available only after the Docker sandbox boundary exists.
- There is no silent host fallback if Docker, the daemon, the configured image, or sandbox capability checks fail.

### Filesystem and privilege isolation

Every verification container uses:

- task repository/worktree mounted read-only at `/workspace`;
- read-only container root filesystem;
- no Docker socket mount;
- fixed non-secret environment variables only;
- non-root UID/GID;
- `--cap-drop ALL`;
- `no-new-privileges=true`;
- writable temporary state restricted to bounded tmpfs.

Verification cannot turn repository tests/build commands into unrestricted host writes.

### Network and resource policy

Every verification run is bounded by:

- `--network none`;
- CPU limit;
- memory limit and matching memory-swap ceiling;
- PID/process limit;
- `/dev/shm` limit;
- tmpfs size limit;
- host-side wall-clock timeout.

Timeout maps to the existing `SANDBOX_TIMEOUT` failure type. DevFlow assigns a unique container name and performs `docker rm -f` after timeout so terminating the Docker CLI cannot leave untrusted verification code running.

Docker preflight fails closed for rootless daemons and missing/`none` cgroup drivers because Step 3.1 only accepts CPU/memory/PID limits when the runtime can treat them as enforceable evidence.

### Verification-image identity and supply boundary

- Runtime uses `--pull never`; verification never downloads dependencies/images implicitly.
- The trusted baseline verification image is built from a Python 3.11.15 slim-bookworm base pinned by digest and installs pinned pytest/Ruff versions.
- Project-specific dependencies must be baked into a trusted project verification image before execution; verification itself remains offline.
- Preflight resolves the configured image tag/reference to an immutable `sha256:<64 hex>` image ID.
- `docker run` uses that exact image ID rather than the mutable tag, closing the inspect-to-run image-tag TOCTOU window.
- `--entrypoint ""` clears image-provided ENTRYPOINT so a verification image cannot intercept/replace the deterministic command selected by DevFlow.

### Evidence preservation

`CheckResult` now records:

- execution backend (`HOST` / `DOCKER`);
- configured image reference;
- exact immutable image ID;
- cgroup driver;
- cleared-entrypoint evidence;
- read-only workspace/rootfs policy;
- network mode;
- CPU/memory/PID/tmpfs limits;
- capability/no-new-privileges policy;
- effective container user;
- stdout/stderr/exit code/duration.

`FailureReport` includes these execution facts, preserving the existing evidence-driven failure chain.

Known checks retain their existing failure taxonomy:

- pytest non-zero → `TEST_FAILURE`;
- Ruff non-zero → `LINT_FAILURE`;
- sandbox deadline → `SANDBOX_TIMEOUT`;
- Docker/capability/image/start failures → `TOOL_FAILURE`.

Ruff is normalized to `ruff check --no-cache ...` so deterministic lint does not attempt to create `.ruff_cache` in the read-only repository.

### Real Docker acceptance evidence

GitHub Actions builds the verification image before running the suite. Dedicated real-Docker integration tests are not permitted to skip in CI.

The final accepted suite proves with real containers that:

- default pytest verification actually runs inside Docker;
- project-specific custom commands run only through the sandbox;
- an untrusted command cannot create a file in the host task worktree;
- outbound socket access is blocked under `--network none`;
- timeout is classified as `SANDBOX_TIMEOUT` and the container is removed afterward;
- immutable image-ID and cleared-entrypoint evidence is present;
- existing Orchestrator, Repair, parallel-worker, scope, Reviewer, and Phase 2 behavior remains green with Docker as the default verification backend.

Acceptance history:

- First behavior candidate: image build + Ruff passed, pytest **214 passed / 4 failed**. Failures exposed Ruff cache writes under the new read-only workspace; the sandbox was kept strict and Ruff was changed to `--no-cache`.
- Next candidate: **214 passed / 5 skipped**. The five skipped cases were the dedicated Docker tests; CI was tightened so sandbox unavailability is a hard failure rather than a skip.
- Final architecture hardening bound execution to immutable image IDs and cleared image ENTRYPOINT.
- Final verification image build: **PASS**.
- Final `ruff check .`: **PASS**.
- Final `pytest`: **220 passed in 29.16s, 0 skipped**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

### Step 3.1 frozen boundary

Step 3.1 intentionally did **not** introduce:

- Context Packet construction;
- AST/import-aware extraction;
- PostgreSQL;
- Redis/Dramatiq;
- lease/heartbeat/run-token behavior;
- frontend behavior;
- changes to DAG/scheduler/worktree/merge/conflict/Human Gate semantics.

The accepted trust boundary is now:

```text
Task Worktree
      ↓
Git Scope Gate
      ↓
Deterministic Verifier
      ↓
Docker Sandbox Runner
      ├── readonly workspace/rootfs
      ├── offline network
      ├── non-root / no capabilities
      ├── CPU / memory / PID / tmpfs limits
      ├── immutable verification image ID
      ├── bounded timeout + cleanup
      └── stdout / stderr / exit / duration evidence
      ↓
VerificationResult
      ↓
FailureReport / Reviewer
```

---

## Step 3.2 — Context Packet Builder — ACCEPTED

Merged through PR #19: `Phase 3 Step 3.2: add bounded Context Packet Builder`.

Squash merge commit:

`2a4d948b29045be3cbcf5ffae4f541d9135457bb`

### Runtime-owned bounded context artifact

Step 3.2 replaces per-Agent ad hoc repository context assembly with a single runtime-owned `ContextPacketBuilder` boundary:

```text
TaskContract
      +
Trusted managed worktree state
      ↓
ContextPacketBuilder
      ├── task objective / acceptance criteria / scopes
      ├── selected repository files and snippets
      ├── Git/path/scope provenance
      ├── deterministic ordering
      ├── explicit content budgets
      ├── truncation / omission evidence
      └── canonical fingerprint
      ↓
ContextPacket
      ↓
Developer / Repair / Reviewer
```

Repository file content inside a packet is explicitly **untrusted model data**. Runtime-generated Task/Git/scope/budget/provenance/fingerprint fields remain control-plane evidence.

### Deterministic Step 3.2 selection policy

Step 3.2 deliberately avoids semantic retrieval. Candidate ordering is mechanical and reproducible:

1. current Git-changed visible files;
2. writable-scope visible files;
3. read-only-scope visible files;
4. readable-scope visible files;
5. repository-relative path as the stable tie-breaker.

A selected file records:

- repository-relative path;
- tracked/untracked state;
- current changed-state fact;
- all matching TaskContract scope patterns;
- deterministic selection reasons;
- exact selected snippet line range;
- SHA-256 of the complete current source bytes;
- source character/byte size;
- selected character/token-unit usage;
- explicit truncation status.

The builder inventories only managed Git worktree state through `LocalGitWorkspace`; `.git` internals and symbolic-link traversal remain outside the accepted path boundary.

### Budget and truncation semantics

`ContextBudget` independently bounds:

- selected file count;
- characters per selected file;
- total selected characters;
- conservative token units;
- maximum source-file bytes inspected as text.

The accepted provider-neutral estimator is explicitly named:

`utf8_bytes_upper_bound`

It records selected UTF-8 byte count as a conservative budgeting unit. DevFlow does **not** claim this is an exact SiliconFlow/model tokenizer count. A future model-specific tokenizer must be an explicit policy change rather than a silent semantic change.

Step 3.2 uses deterministic prefix snippets. When content cannot be included completely, the packet records structured evidence rather than silently losing context:

- `PER_FILE_CHAR_LIMIT`;
- `TOTAL_CHAR_LIMIT`;
- `TOKEN_BUDGET`;
- `FILE_COUNT_LIMIT`;
- `SOURCE_FILE_TOO_LARGE`;
- `NON_UTF8`;
- `PATH_UNAVAILABLE`.

Developer and Repair retain their accepted controlled repository tools for additional task-visible reads when a bounded packet is insufficient. Reviewer remains tool-less and continues to receive actual Git diff plus deterministic verification evidence.

### Canonical packet integrity

Each `ContextPacket` carries a SHA-256 fingerprint over its canonical runtime payload, including task projection, Git identity, selected files/snippets, provenance, budget, usage, and truncation evidence.

`ContextPacket` re-computes the canonical fingerprint during Pydantic validation. Therefore:

- mutating a packet payload while keeping an old fingerprint fails validation;
- supplying a detached/forged fingerprint fails validation;
- identical task + worktree state + budget produces the same packet/fingerprint;
- relevant repository-content changes produce a different fingerprint.

Developer, Reviewer, and Repair additionally validate that a supplied packet matches the complete TaskContract projection represented in the packet:

- task id;
- objective;
- acceptance criteria;
- readable scope;
- writable scope;
- read-only scope.

A caller cannot use a same-id packet with altered task semantics to widen context or scope silently.

### Stage-local rebuild from current repository truth

Production `SingleTaskOrchestrator` owns packet construction and rebuilds context from current worktree truth at every Agent stage boundary:

1. immediately before initial Developer execution;
2. immediately before each targeted Repair attempt;
3. after deterministic verification passes and immediately before independent Reviewer execution.

The accepted end-to-end test demonstrates three distinct repository states:

```text
Developer packet: VALUE = 1
        ↓ Developer mutation
Repair packet:    VALUE = 3
        ↓ targeted Repair
Reviewer packet:  VALUE = 2
```

The three packets have distinct fingerprints. Repair/Reviewer therefore do not inherit stale Developer conversation context as repository truth.

Context construction is offloaded through `asyncio.to_thread`, preserving the accepted bounded parallel-worker event-loop behavior. Context build/validation failure becomes non-retryable runtime `TOOL_FAILURE` evidence and cannot be converted into success.

### Step 3.2 acceptance evidence

Dedicated tests cover:

- deterministic changed/writable/read-only/readable/path ordering;
- path/scope/Git provenance;
- same-state deterministic packet/fingerprint;
- content-change fingerprint movement;
- forged/detached fingerprint rejection;
- per-file, total, file-count, token-unit, and source-size boundaries;
- multibyte UTF-8 conservative budgeting;
- non-UTF-8 omission with explicit evidence;
- current-state packet rebuild across Developer → Repair → Reviewer;
- Reviewer remains `tools=[]`.

Final validation:

- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- `pytest`: **228 passed in 27.17s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**;
- all accepted V0.1, Phase 2, and Step 3.1 regression behavior remained green.

### Step 3.2 frozen boundary

Step 3.2 intentionally did **not** introduce:

- AST traversal or import/dependency expansion;
- symbol ranking / call-graph analysis;
- embeddings or vector retrieval;
- PostgreSQL;
- Redis/Dramatiq;
- distributed lease/heartbeat/run-token behavior;
- frontend behavior;
- changes to DAG/scheduler/worktree/merge/conflict/Human Gate semantics.

The packet contract, provenance, budget, canonical fingerprint, and stage-local rebuild semantics are now frozen as the input boundary that later context-selection improvements must preserve.

---

## Gate before Step 3.3 — AST / Import-aware Relevant-code Extraction

Step 3.3 may improve **which repository regions are selected as relevant context**, but it must build on the accepted Step 3.2 `ContextPacket` contract rather than replacing or bypassing it.

Required direction:

```text
TaskContract
      +
Trusted repository state
      ↓
AST / Import-aware Extraction
      ├── language-aware symbols
      ├── imports / local dependency edges
      ├── task-scope filtering
      ├── deterministic relevance evidence
      └── bounded selected regions
      ↓
ContextPacketBuilder
      ├── existing budget enforcement
      ├── existing path/provenance metadata
      ├── truncation / omission evidence
      └── canonical fingerprint
      ↓
Developer / Repair / Reviewer
```

Step 3.3 must preserve:

- TaskContract as the authority for readable/writable/read-only scope;
- current managed worktree/Git state as the repository source of truth;
- repository contents as untrusted model data;
- deterministic, inspectable selection evidence;
- Step 3.2 content budgets and truncation semantics;
- canonical packet fingerprint validation;
- stage-local packet rebuild before Developer, Repair, and Reviewer;
- existing sandbox, verification, review, repair, DAG, worktree, merge, and Human Gate boundaries.

Step 3.3 may add AST/import-aware extraction and relevance ranking needed to replace deterministic prefix-only selection, but it must not introduce PostgreSQL, Redis/Dramatiq, distributed leases, frontend behavior, embeddings/vector retrieval unless the development plan is deliberately amended.

**Step 3.3 status: NOT STARTED.**
