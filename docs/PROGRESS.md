# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*.

## Current position

- Current phase: **Phase 2 — V0.2 True Multi-Agent Runtime**
- Completed item: **Step 2.4 — Parallel Worker Execution**
- Next item: **Step 2.5 — Topological Merge Queue**
- Phase 2 status: **IN PROGRESS**
- V0.1 status: **ACCEPTED / COMPLETE**

## Phase 1 — V0.1 Single Task Evidence Loop — ACCEPTED

Phase 1 was completed through PR #1–#10. Detailed implementation and acceptance evidence remain preserved in those PR descriptions and Git history.

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

---

# Phase 2 — V0.2 True Multi-Agent Runtime

Goal: safely support dependent and eventually parallel tasks while preserving every V0.1 evidence, scope, verification, review, and repair boundary.

Planned order:

1. Task DAG representation.
2. `READY/RUNNING/SUCCEEDED/FAILED/BLOCKED` scheduler.
3. Git Worktree per task.
4. Parallel worker execution.
5. Topological merge queue.
6. Merge conflict classification.
7. Integration/Human Gate strategy.

## Step 2.1 — Task DAG Representation — ACCEPTED

Merged through PR #11: `Phase 2 Step 2.1: add validated task DAG representation`.

Delivered:

- `TaskNode` wraps the accepted V0.1 `TaskContract` with immutable dependency edges.
- `TaskDAG` rejects duplicate task ids, unknown dependencies, duplicate dependencies, self-dependencies, and cycles.
- Deterministic topological ordering.
- Deterministic ready-task derivation.
- Transitive blocked-task derivation from failed dependencies.
- Runtime graph queries fail closed on contradictory completed/failed state.
- DAG task/dependency collections are immutable tuples, closing the Pydantic shallow-freeze mutation gap.

Acceptance evidence:

- `ruff check .`: **PASS**.
- `pytest`: **120 passed in 6.67s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- All accepted V0.1 tests remained green.
- No Scheduler, Worktree, worker pool, merge queue, or conflict handling was introduced early.

## Step 2.2 — DAG Scheduler — ACCEPTED

Merged through PR #12: `Phase 2 Step 2.2: add deterministic DAG scheduler`.

Delivered:

- Added scheduler lifecycle states: `PENDING`, `READY`, `RUNNING`, `SUCCEEDED`, `FAILED`, `BLOCKED`.
- `DAGScheduler` is the single owner of mutable scheduling state for one immutable validated `TaskDAG`.
- Zero-dependency tasks become `READY` deterministically at initialization.
- Only `READY` tasks may become `RUNNING`.
- Only `RUNNING` tasks may become `SUCCEEDED` or `FAILED`.
- Successful tasks trigger deterministic readiness recomputation; a dependent becomes `READY` only when every dependency is `SUCCEEDED`.
- Failed tasks propagate `BLOCKED` transitively to downstream pending tasks.
- Independent runnable branches remain unaffected by another branch failure.
- Multi-parent tasks stay blocked once any required dependency fails, even if another parent later succeeds.
- Centralized allowed-transition rules reject illegal lifecycle moves before mutation, including `PENDING -> RUNNING`, `READY -> BLOCKED`, terminal restarts, and `BLOCKED -> READY`.
- Failure propagation pre-validates the full blocked descendant set before changing the failed task, preventing partial state updates if an invariant is violated.
- Added immutable `TaskScheduleRecord`, `SchedulerEvent`, and `SchedulerSnapshot` evidence models.
- Scheduler snapshots/events are deterministic and do not expose the internal mutable state mapping.
- Multiple independent tasks may be represented as `RUNNING` for future parallelism, but Step 2.2 launches no Agent or Worker.

Acceptance evidence:

- First CI candidate exposed one Ruff line-length error; pytest did not run until the static gate was fixed.
- Second candidate: `ruff check .` **PASS**, `pytest` **135 passed**.
- Pre-merge invariant review tightened the centralized transition table and disallowed revoking an already-`READY` task into `BLOCKED`.
- Final `ruff check .`: **PASS**.
- Final `pytest`: **135 passed in 6.36s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- All V0.1 and Step 2.1 tests remained green.
- No Git Worktree, Agent launch, async/thread/process worker execution, merge queue, Redis/Dramatiq, or merge-conflict behavior was introduced prematurely.

## Step 2.3 — Git Worktree per task — ACCEPTED

Merged through PR #13: `Phase 2 Step 2.3: add isolated Git worktree per task`.

Delivered:

- Added `TaskWorktreeManager` as the owner of task-linked-worktree lifecycle for one clean base repository.
- Manager freezes an immutable run base commit from the clean base repository before task worktrees are created.
- Task branch and filesystem identities use a sanitized slug plus deterministic SHA-256 task-id suffix rather than trusting raw task ids as Git ref names.
- Generated task branches are checked through Git ref-format validation before creation.
- Task worktrees are created as locked linked worktrees on fresh task branches and are post-verified to be registered, locked, clean, on the expected branch, and at the exact requested commit.
- Default task worktrees start from the frozen run base even if the base repository HEAD later advances.
- An optional explicit full commit SHA can be used as a later task base only if it exists and is a descendant of the frozen run base. This provides a safe primitive for future dependency-resolved task creation without implementing dependency integration in Step 2.3.
- `open_workspace()` returns the existing `LocalGitWorkspace`, preserving the already accepted path, `.git`, symlink, scope, tool, and verifier boundaries inside each linked worktree.
- Existing registered worktrees, pre-existing task branches, unregistered filesystem collisions, missing worktree directories, and stale/prunable registrations fail closed instead of being silently reused or overwritten.
- The worktree root must be outside the base repository and is validated before directory creation, so an invalid configuration does not itself dirty the target repository.
- Removal is restricted to the exact manager-owned path/branch registration.
- Dirty worktree removal is refused by default; destructive cleanup requires explicit `force=True`.
- Removing a worktree deliberately preserves its task branch, including committed task output, so later merge-queue stages do not lose completed task evidence.

Isolation and lifecycle acceptance evidence:

- Real Git test creates two linked task worktrees from the same run base; an uncommitted edit in TASK-A is invisible to TASK-B and the base repository.
- Real Git test confirms default later task worktrees remain pinned to the frozen run base after the base repository HEAD advances.
- Real Git test confirms an explicit descendant commit can be selected as a task base, while an unrelated commit and abbreviated SHA are rejected.
- Duplicate task creation cannot overwrite or reuse existing branch/path state.
- Missing linked-worktree directories are surfaced as stale registrations rather than automatically pruned/recreated.
- Dirty base repositories cannot initialize or continue worktree creation.
- Invalid in-repository worktree roots fail without creating `.devflow` or other repository pollution.
- Clean removal, explicit forced dirty removal, and branch-preserving cleanup are exercised with real Git.
- A real commit made inside a task worktree remains reachable from the preserved task branch after the linked worktree directory is removed.
- First CI candidate was stopped by four Ruff line-length errors before pytest; no behavior test was bypassed.
- Second candidate: `ruff check .` **PASS**, `pytest` **148 passed**.
- Pre-merge architecture review added validated descendant task-base support and branch-preservation/root-pollution regression tests for later DAG integration.
- Final `ruff check .`: **PASS**.
- Final `pytest`: **153 passed in 7.13s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Tests use real Git linked worktrees rather than mocked `git worktree` commands.
- All accepted V0.1, Step 2.1, and Step 2.2 tests remained green.
- No Worker pool, concurrent Agent execution, merge queue, dependency integration, merge-conflict classification, Redis/Dramatiq, or Docker behavior was introduced prematurely.

## Step 2.4 — Parallel Worker Execution — ACCEPTED

Merged through PR #14: `Phase 2 Step 2.4: add bounded parallel worker execution`.

Delivered:

- Added `ParallelWorkerCoordinator` as the bounded executor for one deterministic snapshot of scheduler-`READY` tasks.
- `asyncio.Semaphore` enforces `max_concurrency`; a READY task is not moved to `RUNNING` until it has actually acquired a worker slot.
- The Coordinator rejects overlapping/re-entrant `run_ready_wave()` calls so separate callers cannot bypass the configured concurrency bound on the same runtime object.
- Each worker creates exactly one manager-owned linked worktree and runs a task-specific V0.1 runner inside that isolated `LocalGitWorkspace`.
- The accepted `SingleTaskOrchestrator` remains the execution path inside each worker, preserving Developer → deterministic verification → independent Reviewer → targeted Repair semantics.
- Blocking deterministic verification is offloaded through `asyncio.to_thread()`, allowing pytest/Ruff verification from independent task worktrees to overlap without serially blocking the shared asyncio event loop.
- V0.1 terminal evidence maps deterministically back into scheduler `SUCCEEDED` / `FAILED` state.
- Worker exceptions and task-level cancellation produce explicit non-retryable runtime failure evidence and cannot leave scheduler tasks falsely stuck in `RUNNING`.
- Failure in one independent worker does not cancel unrelated runnable branches; existing DAG dependency propagation remains the only mechanism that blocks descendants.
- Added immutable `WorkerTaskResult` and `ParallelWorkerWaveResult` evidence models, including worktree path, task branch, task base commit, finalized task commit, peak concurrency, and final scheduler snapshot.
- Successful task output is committed only to the worker's task branch; no task worker merges into the base repository.
- `TaskWorktreeManager.commit_task_changes()` finalizes successful output with `git add --all`, `write-tree`, `commit-tree`, and atomic `update-ref`, avoiding repository-controlled normal commit hooks while preserving a concrete task-branch commit for the future merge queue.
- Base repository HEAD and files remain unchanged while successful task branches advance independently.
- Newly-ready downstream tasks are returned as `next_ready_task_ids` but are not automatically executed in the same worker wave.
- A dependent READY task cannot be executed from the stale frozen run base. A later wave requires an externally resolved full descendant integration commit through `task_base_resolver`.
- The supplied downstream base is still revalidated by `TaskWorktreeManager` for commit existence and run-base ancestry before a task worktree is created.
- Step 2.4 deliberately does not create integration commits; the resolver is only the safe handoff boundary that Step 2.5 may satisfy.

Parallel execution acceptance evidence:

- A three-root-task test with `max_concurrency=2` proves two tasks are simultaneously `RUNNING` while the third remains `READY` until a slot is available.
- Real linked-worktree tests prove overlapping workers cannot see one another's uncommitted changes and do not mutate the base repository.
- A same-Coordinator re-entry regression test rejects two overlapping READY waves.
- Newly-unlocked dependent tasks remain READY after the current wave rather than being executed from the wrong base commit.
- A follow-up wave without a dependency integration base fails before scheduler mutation or worktree creation.
- A synthetic descendant integration commit demonstrates that the future Step 2.5 handoff can safely enable a later dependent worker wave.
- One failed independent branch blocks only its DAG descendants while another branch can still succeed and unlock its own dependent.
- Runner exception and cancellation tests prove task state is terminalized with structured evidence instead of remaining `RUNNING`.
- Successful-worker tests prove the task branch receives a real commit while base HEAD is unchanged and the finalized worktree is clean.
- Two real V0.1 `SingleTaskOrchestrator` instances execute concurrently with real Developer tool calls, real Git linked worktrees, real pytest/Ruff verification, independent semantic review, and independent task-branch commits.
- A thread-barrier verifier regression proves synchronous deterministic verification is truly offloaded and can overlap across workers.
- First complete candidate: `ruff check .` **PASS**, `pytest` **162 passed**.
- Architecture hardening added non-reentrant waves and the dependent-task integration-base gate; next candidate: **164 passed**.
- Final handoff regression added descendant-base execution coverage.
- Final `ruff check .`: **PASS**.
- Final `pytest`: **165 passed in 8.61s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- No real SiliconFlow API call was required by CI.
- All accepted V0.1 and Phase 2 Step 2.1–2.3 tests remained green.
- No topological merge queue, dependency integration implementation, merge-conflict classification, Redis/Dramatiq, or Docker behavior was introduced prematurely.

## Gate before Step 2.5 — Topological Merge Queue

Step 2.5 may now integrate committed successful task branches in deterministic DAG/topological order and produce trusted descendant integration commits for later dependent worker waves.

Required direction:

```text
successful WorkerTaskResult(s)
      ↓
committed task branches
      ↓
Topological Merge Queue
      ↓
current integration commit
      ↓
validate task branch / expected task commit
      ↓
integrate eligible task branch
      ↓
new integration commit
      ↓
task_base_resolver for newly READY dependents
```

Step 2.5 should establish:

- one explicit integration head/base owned by the merge queue rather than advancing worker task branches or trusting the base working tree implicitly;
- deterministic merge eligibility/order derived from the validated DAG and successful worker evidence;
- only scheduler-`SUCCEEDED` tasks with an exact expected finalized task-branch commit may enter the queue;
- task branch identity and commit ancestry must be revalidated immediately before integration;
- an already-integrated task must not be merged twice;
- integration must preserve committed task-branch evidence rather than deleting or rewriting worker history;
- each successful integration must produce an inspectable descendant commit that can be passed to Step 2.4 as the base for newly READY dependent tasks;
- the base repository working tree must remain clean and integration state must be deterministic/recoverable from Git evidence;
- merge/integration failure must stop safely without pretending the task was integrated.

Step 2.5 must **not** add rich merge-conflict diagnosis/repair policy; detailed `MERGE_CONFLICT` classification belongs to Step 2.6. It must also not add Redis/Dramatiq persistence, Docker sandboxing, or the final Integration/Human Gate strategy.