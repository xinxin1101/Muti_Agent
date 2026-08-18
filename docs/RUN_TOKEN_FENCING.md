# Phase 3 / Step 3.7 — run_token Stale-Write Protection

## Problem

Step 3.6 can identify an abandoned execution after its PostgreSQL lease expires, but liveness alone
cannot prevent the old process from reaching PostgreSQL or Git later. Safe ownership transfer therefore
requires a fencing generation that every worker-owned mutable boundary can validate.

Step 3.7 closes that gap without changing the existing success authority:

> **A lease decides whether an execution generation is live; `run_token` decides whether a mutable
> worker write belongs to that live generation.**

`run_token` is authorization/fencing evidence. It is not task-success evidence.

## Ownership generation

Each persisted task stores:

```text
lease_generation
run_token
lease_owner
lease_dispatch_id
lease_acquired_at
heartbeat_at
lease_until
lease_released_at
```

An unowned task begins at generation `0` with no token.

Initial acquisition creates:

```text
generation = 1
run_token = fresh UUID
```

If generation 1 becomes EXPIRED while its Run remains RUNNING, a later acquisition may atomically
replace it with:

```text
generation = 2
run_token = fresh UUID
new worker_id / dispatch_id / timestamps
```

The old token is never reused. ACTIVE ownership is exclusive and RELEASED ownership remains terminal
history; neither can be silently taken over.

The token is returned directly from PostgreSQL acquisition to the worker process as a
`TaskLeaseGrant`. It is deliberately excluded from `TaskLeaseSnapshot` and from normal model
serialization/repr. Redis messages remain exactly:

```text
dispatch_id
run_id
task_id
```

The queue cannot grant, replay, or manufacture a fencing token.

## Database-time liveness

Token equality is necessary but not sufficient. Worker-owned mutable writes require both:

1. the exact current `run_token`; and
2. a lease that is still ACTIVE according to PostgreSQL `clock_timestamp()`.

Therefore an old worker loses write authority as soon as its lease deadline passes, even if no
replacement worker has acquired the next generation yet.

## PostgreSQL write fencing

Once a task has an acquired generation, task-scoped worker writes are fenced at the persistence
boundary.

The following operations require the current live token:

- task-scoped runtime/Developer/verification/Reviewer/Repair/failure evidence;
- dispatch/worker execution evidence;
- single-task terminal finalization;
- context references when written for a leased task;
- lease heartbeat and release.

Fencing happens before persistence idempotency lookup. Consequently a stale worker cannot turn a late
retry into an accepted idempotent write merely because the same evidence key already exists.

Step 3.4 local/non-leased persistence remains compatible: a task that has never acquired a lease has
no `run_token` and continues to use the existing persistence API without one. Once ownership is
acquired, the task switches permanently onto the fenced worker-write path.

## Git publication fencing

A stale process can also be dangerous if it can publish a late Git task branch after its lease expires.
Step 3.7 therefore fences the runtime-owned Git publication boundary, not only PostgreSQL writes.

Before `TaskWorktreeManager.commit_task_changes()` publishes its branch ref, the backend enters
`PostgresTaskLeaseStore.guard_task_publication()`.

The guard:

1. locks the Run row;
2. requires the Run to remain RUNNING;
3. locks the task row;
4. validates the current live token and dispatch;
5. keeps those PostgreSQL locks while the bounded Git commit/ref publication runs.

Holding the task-row lock across `git update-ref` closes the token-check/use race: an expired-owner
takeover cannot install generation N+1 between generation N's final ownership check and its Git ref
publication. If N enters the guard while still live, its publication linearizes before any later
takeover. If N is already expired/stale when it reaches the guard, publication is rejected.

Agents still do not receive unrestricted Git publication capability; this fence protects the
runtime-owned publication path.

## Generation-scoped worktrees

Step 3.5 intentionally used a run/task-scoped worktree identity so duplicate delivery collided
fail-closed before fencing existed. Safe takeover changes that requirement.

Step 3.7 derives queued worktree/branch identity from:

```text
run_id + task_id + run_token generation identity
```

The token itself is not placed in the branch name; only a deterministic digest is used. A new owner
can therefore create a clean generation-specific worktree even if an abandoned worker left its old
worktree registered and dirty. Old generation artifacts remain distinguishable evidence but cannot be
accepted by current-token persistence/publication gates.

## Migration safety

Migration `0003_run_token_fencing` adds `lease_generation` and `run_token`.

Existing Step 3.6-owned rows are backfilled during upgrade with generation `1` and a database-generated
fresh token. A pre-upgrade worker never received that token, so after migration it cannot use the new
fenced APIs as an accidental compatibility bypass.

Schema constraints require:

- UNOWNED -> generation `0`, token null, no ownership timestamps;
- owned history -> generation at least `1`, token non-null, complete ownership fields;
- `run_token` is unique across task rows.

## What Step 3.7 does not claim

Step 3.7 does not claim exactly-once execution. Work may still be computed more than once after a
worker failure. The guarantee is narrower and more useful:

> **Only the current live execution generation may publish worker-owned mutable state through DevFlow's
> accepted PostgreSQL and Git publication boundaries.**

This step does not add structured streaming/observability, frontend/SSE, embeddings/vector retrieval,
Agent/AST/RAG expansion, generic automatic merge repair, or a new scheduler.

## Frozen source-of-truth boundary

- Redis/Dramatiq: delivery transport only.
- PostgreSQL: durable runtime/ownership/fencing evidence.
- Git/worktree: repository and code-state truth.
- deterministic verifier + Reviewer + Git commit evidence: success gates.
- `run_token`: current-generation write authorization only.
