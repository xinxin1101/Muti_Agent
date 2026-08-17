# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*.

## Current position

- Current phase: **Phase 2 — V0.2 True Multi-Agent Runtime**
- Completed item: **Step 2.3 — Git Worktree per task**
- Next item: **Step 2.4 — Parallel Worker Execution**
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

## Gate before Step 2.4 — Parallel Worker Execution

Step 2.4 may now connect accepted scheduler readiness to isolated task worktrees and execute multiple independent READY tasks concurrently.

Required direction:

```text
Validated TaskDAG
      ↓
DAGScheduler
      ↓
READY task set
      ↓
Bounded Worker Coordinator
   ├── TASK-A → isolated TaskWorktree → V0.1 SingleTaskOrchestrator
   └── TASK-B → isolated TaskWorktree → V0.1 SingleTaskOrchestrator
      ↓
per-task terminal evidence
      ↓
DAGScheduler.succeed()/fail()
```

Step 2.4 should establish:

- a bounded concurrency limit rather than unbounded task fan-out;
- only scheduler-`READY` tasks may acquire a worker and become `RUNNING`;
- each worker is bound to exactly one manager-owned task worktree;
- every task continues to run the accepted V0.1 Developer → Verify → Reviewer → Repair evidence loop inside its own worktree;
- worker completion maps terminal V0.1 evidence back to scheduler `SUCCEEDED` / `FAILED` deterministically;
- one worker failure must not cancel unrelated independent runnable branches unless the DAG dependency rules require blocking;
- task exceptions/cancellation must not leave scheduler state falsely `RUNNING` without explicit failure evidence;
- concurrency tests must prove tasks overlap in time while their filesystem changes remain isolated.

Step 2.4 must **not** merge task branches, implement the topological merge queue, classify Git merge conflicts, add Redis/Dramatiq persistence, or introduce the later Integration/Human Gate. Those remain subsequent Phase 2 milestones.
