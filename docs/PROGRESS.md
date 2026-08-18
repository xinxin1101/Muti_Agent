# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*. Detailed implementation discussions remain preserved in the corresponding pull requests and Git history.

## Current position

- Current phase: **Phase 3 — V0.3 Safety, Context and Reliability**
- Completed item: **Step 2.7 — Integration / Human Gate Strategy**
- Next item: **Step 3.1 — Docker Verification Sandbox**
- Phase 2 status: **ACCEPTED / COMPLETE**
- Phase 3 status: **NOT STARTED**
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

Goal: safely support dependent and parallel tasks while preserving every V0.1 scope, verification, review, repair, and evidence boundary.

## Step 2.1 — Task DAG Representation — ACCEPTED

Merged through PR #11: `Phase 2 Step 2.1: add validated task DAG representation`.

Key accepted boundaries:

- Immutable `TaskNode` / `TaskDAG` representation.
- Duplicate ids, unknown dependencies, duplicate dependencies, self-dependencies, and cycles fail validation.
- Deterministic topological ordering and ready-task derivation.
- Transitive blocked-task derivation from failed dependencies.
- Contradictory runtime graph state fails closed.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **120 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.2 — DAG Scheduler — ACCEPTED

Merged through PR #12: `Phase 2 Step 2.2: add deterministic DAG scheduler`.

Key accepted boundaries:

- Scheduler states: `PENDING`, `READY`, `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`.
- Only `READY` tasks may start; only `RUNNING` tasks may terminalize.
- Dependency success deterministically unlocks downstream tasks.
- Failure propagates `BLOCKED` transitively without cancelling independent branches.
- Centralized transition rules reject illegal state changes before mutation.
- Immutable scheduler snapshots/events expose evidence without leaking mutable scheduler state.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **135 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.3 — Git Worktree per Task — ACCEPTED

Merged through PR #13: `Phase 2 Step 2.3: add isolated Git worktree per task`.

Key accepted boundaries:

- One manager-owned linked Git worktree per task.
- Task branches and filesystem names use sanitized identities plus deterministic hashes.
- Worktrees start from a frozen run base unless an explicit full descendant integration commit is supplied.
- Duplicate branch/path state, stale registrations, invalid roots, and dirty base repositories fail closed.
- Existing `LocalGitWorkspace` path, `.git`, symlink, scope, and verification protections remain active inside each worktree.
- Removing a worktree preserves its task branch and committed evidence.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **153 passed**.
- Real Git linked-worktree tests.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.4 — Parallel Worker Execution — ACCEPTED

Merged through PR #14: `Phase 2 Step 2.4: add bounded parallel worker execution`.

Key accepted boundaries:

- `ParallelWorkerCoordinator` executes a deterministic READY wave under `asyncio.Semaphore` concurrency bounds.
- A task moves to `RUNNING` only after acquiring a worker slot.
- Re-entrant/overlapping waves on the same coordinator are rejected.
- Each worker executes the accepted V0.1 `SingleTaskOrchestrator` in its own linked worktree.
- Blocking deterministic verification is offloaded from the shared asyncio event loop.
- Worker exception/cancellation terminalizes structured failure evidence instead of leaving tasks stuck `RUNNING`.
- Successful work is committed only to the task branch; base repository files and HEAD remain unchanged.
- Downstream tasks require a trusted descendant integration commit; they never silently start from stale run base state.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **165 passed**.
- Real concurrent V0.1 workers, Git worktrees, verification, review, and task commits.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.5 — Topological Merge Queue — ACCEPTED

Merged through PR #15: `Phase 2 Step 2.5: add topological merge queue`.

Key accepted boundaries:

- Integration head lives under `refs/devflow/integration/<integration_id>` and starts at the frozen run base.
- Worker results are validated at admission and immediately before integration, including exact branch/base/task commit identity.
- Integration order follows validated DAG topology rather than caller result ordering.
- Object-level `git merge-tree --write-tree` + `git commit-tree` + compare-and-swap `git update-ref` avoids using the base working tree/index as a merge workspace.
- Integration history is recoverable and revalidated from Git parents, metadata, and recomputed deterministic merge trees.
- A real conflict does not advance integration head. It creates a durable tree-neutral marker under `refs/devflow/integration-conflicts/<integration_id>` and stops the queue.
- Conflict state recovers after process reconstruction.
- External integration-ref/task-branch movement fails closed.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **176 passed**.
- Real Git refs, `merge-tree`, `commit-tree`, `update-ref`, conflict markers, and TOCTOU tests.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.6 — Merge Conflict Classification — ACCEPTED

Merged through PR #16: `Phase 2 Step 2.6: add merge conflict classification`.

Key accepted boundaries:

- Added immutable `MergeConflictEvidence`, per-file conflict stages, native Git messages/types, and structural stage shapes.
- `GitMergeConflictClassifier` consumes a stopped Step 2.5 snapshot and independently revalidates integration ref, conflict marker, exact task branch head, and task commit/base history.
- Classification replays `git merge-tree --write-tree -z --messages` and requires conflict exit code `1`.
- NUL-safe machine parsing preserves spaces and Unicode paths.
- Git stage semantics are explicit: stage 1 = merge base, stage 2 = integration side, stage 3 = task side.
- Structural shapes derive from stage facts only: `THREE_WAY`, `ADD_ADD`, `MODIFY_DELETE`, `DELETE_MODIFY`, `COMPLEX`.
- Human-readable Git prose is bounded evidence, not a control-flow parser input.
- Recovered queue classification is deterministic and identical.
- Classifier remains read-only: no file edits, index writes, conflict resolution, marker deletion, or integration continuation.

Important acceptance discovery:

- Git 2.54 reports an add/add content conflict with machine `conflict-type = CONFLICT (contents)` even though human prose mentions `add/add`.
- DevFlow therefore derives structural `ADD_ADD` from stages `{2,3}` while preserving the native Git machine type unchanged.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **187 passed in 14.92s**.
- Real Git conflict/recovery tests.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 2.7 — Integration / Human Gate Strategy — ACCEPTED

Merged through PR #17: `Phase 2 Step 2.7: add integration human gate strategy`.

Squash merge commit:

`4d091949f1ba5a57465dc30edd4f5935f1a06fdd`

### Delivered policy boundary

- Added explicit integration-gate states: `AUTO_REPAIR_CANDIDATE`, `AWAITING_HUMAN`, `REPAIR_AUTHORIZED`, and `ABORTED`.
- `IntegrationConflictPolicy` evaluates Step 2.6 structured evidence using the **trusted TaskContract stored in the scheduler DAG**. Callers cannot provide a wider same-id contract to expand Agent Repair scope.
- Automatic merge-conflict Repair is **disabled by default**.
- When explicitly enabled, automatic Repair eligibility is deliberately narrow: one conflict file, `THREE_WAY`, regular `100644` stages, only native `CONFLICT (contents)`, narrow text suffix allowlist, trusted writable scope, and no protected path.
- Actual stage blobs are inspected with `git cat-file`; automatic eligibility requires each blob to be at most 512 KB, contain no NUL byte, and strictly decode as UTF-8. A binary blob merely named `*.py` cannot become an automatic Repair candidate.

### Human Gate is not a privilege bypass

Policy separates:

- `route`: automatic Repair candidate versus Human Gate.
- `human_repair_authorizable`: whether a human is allowed to delegate the conflict to Agent Repair at all.

Hard boundaries remain non-bypassable even by explicit human approval:

- out-of-scope paths;
- protected tests/control/configuration paths;
- non-regular stages;
- unsafe, binary, oversized, or non-UTF-8 blobs.

Those cases remain `HUMAN_REQUIRED` with `human_repair_authorizable=False`; Step 2.7 rejects `AUTHORIZE_REPAIR` and permits only `ABORT`.

Protected-path policy covers DevFlow/GitHub/GitLab control directories, any `tests` directory component, `test_*.py`, `*_test.py`, `conftest.py`, `.env`, package manifests/lockfiles, `pyproject.toml`, `uv.lock`, and configured protected basenames.

### Durable evidence-bound decisions

- `conflict_evidence_fingerprint` hashes structured Git conflict facts and deliberately excludes raw/free-form Git prose.
- `integration_policy_fingerprint` separately binds the policy facts that affect Repair authorization.
- A durable human decision cannot be reused merely because a later policy still produces the same coarse `HUMAN_REQUIRED` route.
- Human decisions are explicit `AUTHORIZE_REPAIR` / `ABORT` records stored under `refs/devflow/integration-decisions/<integration_id>`.
- Decision commits are tree-neutral: their sole parent is the exact conflict marker and their tree equals the conflict-marker tree, so decision recording changes no repository code.
- Decision metadata binds conflict marker/ref, integration head, task id/branch/commit, evidence fingerprint, policy fingerprint, actor, note, and decision.
- Actor/note metadata is bounded, single-line, JSON encoded, and validated to prevent metadata injection.
- A second human decision cannot overwrite the first decision audit record.

### Live Gate validation and TOCTOU protection

`IntegrationHumanGate.snapshot()` is not a cached boolean. Each read:

1. re-runs Step 2.6 conflict classification;
2. revalidates structured evidence fingerprint;
3. recomputes policy and validates policy fingerprint;
4. recovers/revalidates any durable human decision;
5. rechecks integration ref, conflict ref, task branch, scheduler state, and base-workspace cleanliness.

Human decision creation uses a single `git update-ref --stdin` transaction that atomically verifies:

- integration ref == reviewed integration head;
- conflict ref == reviewed conflict marker;
- task branch == reviewed task commit;
- decision ref does not already exist and is created only after those verifications succeed.

This closes the human-click TOCTOU window across the three authoritative Git refs.

### Critical semantic boundary

> `repair_may_start=True` means only that a later bounded Repair stage is authorized to begin.

Every Step 2.7 snapshot fixes:

`integration_may_advance=False`

Therefore human approval or policy eligibility is **never** interpreted as proof that a merge conflict has been resolved.

Step 2.7 intentionally does **not**:

- call an LLM conflict resolver;
- edit conflicted files;
- choose `ours` / `theirs`;
- execute merge-conflict Repair itself;
- delete conflict markers;
- restart the merge queue;
- advance the integration ref.

Any future conflict Repair must create new repository-state evidence and pass the already accepted deterministic verification/review boundaries before integration can proceed.

### Step 2.7 acceptance evidence

Real-Git tests cover:

- conservative default Human Gate;
- narrow opt-in automatic Repair eligibility;
- add/add and modify/delete routing;
- trusted-DAG scope binding;
- protected paths and non-bypassable human boundaries;
- binary/NUL blob exclusion even with a `.py` filename;
- durable tree-neutral `AUTHORIZE_REPAIR` and `ABORT` records;
- reconstruction/recovery;
- immutable one-decision semantics;
- metadata injection rejection;
- forged decision-tree rejection;
- integration-ref race rejection;
- task-branch race rejection;
- stale policy authorization rejection through policy fingerprints;
- evidence fingerprint independence from raw/free-form Git text.

Final validation:

- `ruff check .`: **PASS**.
- `pytest`: **205 passed in 20.54s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- All accepted V0.1 and Phase 2 Step 2.1–2.6 tests remained green.

---

## Phase 2 exit criterion — ACCEPTED

V0.2 now satisfies the planned multi-agent runtime boundaries:

- validated Task DAG and deterministic dependency state;
- bounded parallel task execution;
- one isolated Git worktree per task;
- successful task output frozen as auditable task commits;
- deterministic topological object-level integration;
- recoverable merge-conflict markers;
- structured, replayable conflict evidence;
- durable policy + Human Gate authorization that cannot bypass hard Repair boundaries;
- no LLM self-report can mark integration successful or bypass Git evidence.

The runtime now has a complete evidence boundary from parallel task execution through integration conflict triage. **Phase 2 is frozen as ACCEPTED / COMPLETE.**

---

# Phase 3 — V0.3 Safety, Context and Reliability — NOT STARTED

Planned order from `docs/DEVELOPMENT_PLAN.md`:

1. Docker verification sandbox.
2. CPU / memory / network / time limits.
3. Context Packet Builder.
4. AST/import-aware relevant-code extraction.
5. PostgreSQL persistence.
6. Redis + Dramatiq workers.
7. Lease + heartbeat.
8. `run_token` stale-write protection.
9. Structured run/event logs.

## Gate before Step 3.1 — Docker Verification Sandbox

Step 3.1 may now isolate the **deterministic verification execution boundary** without changing the accepted Agent, scope, scheduler, worktree, merge, or Human Gate semantics.

Required direction:

```text
Task worktree
      ↓
Git scope gate
      ↓
Deterministic Verifier
      ↓
Docker Sandbox Runner
      ├── isolated working directory / mount policy
      ├── bounded wall-clock timeout
      ├── bounded CPU / memory / process resources
      ├── controlled network policy
      └── captured exit/stdout/stderr/duration
      ↓
VerificationResult / FailureReport
```

Step 3.1 should establish:

- deterministic verification commands execute inside a controlled container rather than unrestricted host execution;
- repository/worktree identity and task scope remain authoritative outside the model;
- sandbox startup failure, command failure, timeout, and resource-limit failure produce explicit bounded evidence and fail closed;
- stdout/stderr/exit code/duration evidence remains compatible with the accepted verifier models;
- the host must not expose Docker socket, GitHub credentials, SiliconFlow keys, or unrelated filesystem paths to the verification process;
- container lifecycle and cleanup are deterministic and tested;
- existing V0.1/Phase 2 verification behavior remains green when sandboxing is not selected by tests/configuration.

Step 3.1 must **not** pull later Phase 3 work forward. In particular it must not introduce Context Packet construction, PostgreSQL, Redis/Dramatiq, distributed leases, frontend behavior, or automatic merge-conflict Repair.

**Step 3.1 status: NOT STARTED.**
