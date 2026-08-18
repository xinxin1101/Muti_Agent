# Step 3.6 — Task Lease + Heartbeat

## Purpose

Step 3.6 adds durable task-execution ownership and liveness evidence around the accepted Step 3.5
Redis/Dramatiq worker boundary.

```text
Redis / Dramatiq delivery
        ↓
LeasedQueuedTaskWorker
        ↓
PostgreSQL task lease acquisition
        ↓
ACTIVE owner
        ↓
periodic PostgreSQL heartbeat renewal
        ↓
existing QueuedTaskWorker
        ↓
existing Git / runtime / verification / review / repair
        ↓
normal completion → RELEASED

heartbeat stops
        ↓
lease_until passes
        ↓
EXPIRED / abandoned execution evidence
```

A lease answers **who is currently recorded as executing a task and whether that ownership is still
live**. It is not a stale-write fence and it is not a success signal.

## Ownership granularity

Lease state belongs to one `(run_id, task_id)` pair rather than to the whole Run. A multi-task DAG
may legitimately have different independent tasks executing on different workers at the same time.

The `tasks` row stores:

```text
lease_owner
lease_dispatch_id
lease_acquired_at
heartbeat_at
lease_until
lease_released_at
```

The task primary key remains `(run_id, task_id)`. Step 3.6 does not add `run_token` or any equivalent
fencing generation.

## Time authority

Workers do not decide lease expiry using their local wall clocks. `PostgresTaskLeaseStore` uses
PostgreSQL `clock_timestamp()` as the authority for:

- acquisition time;
- heartbeat time;
- lease deadline comparison;
- release time;
- inspection time.

This avoids allowing clock skew between worker hosts to decide ownership liveness.

## State semantics

```text
UNOWNED
   ↓ acquire
ACTIVE
   ├── valid heartbeat → ACTIVE with a later lease_until
   ├── normal completion → RELEASED
   └── no heartbeat before deadline → EXPIRED
```

### UNOWNED

No worker has acquired execution ownership for this persisted task.

### ACTIVE

A worker/dispatch identity owns the lease and `lease_until` is later than PostgreSQL's observation
time.

Only the exact `(owner_id, dispatch_id)` identity may renew or release the lease.

### RELEASED

The owner completed or aborted while the lease was still live and explicitly released ownership.
Release is idempotent for the same owner/dispatch identity. A released lease is retained as durable
ownership evidence rather than deleted.

### EXPIRED

The latest heartbeat deadline is at or before PostgreSQL's observation time and the lease was not
released.

`EXPIRED` means:

> the coordinator may regard this execution attempt as **abandoned for recovery planning**.

It explicitly does **not** mean:

> the old worker has been technically prevented from modifying Git or PostgreSQL.

That second guarantee requires Step 3.7 stale-write fencing.

## No takeover in Step 3.6

Step 3.6 deliberately does not reassign an `EXPIRED` lease to a new worker. It also does not reuse a
`RELEASED` lease row for another execution attempt.

This is intentionally conservative. Without a fencing generation/token, reassigning an expired task
could create this unsafe state:

```text
Worker A lease expires
        ↓
Worker B acquires task
        ↓
Worker A is actually still running
        ↓
A and B can both write
```

Therefore Step 3.6 treats any existing lease history as non-reacquirable. Safe expired-owner takeover
is deferred until Step 3.7 can bind writes to a fresh `run_token` and reject stale generations.

## Worker heartbeat wrapper

`LeasedQueuedTaskWorker` wraps the accepted Step 3.5 `QueuedTaskWorker`:

1. acquire the task lease;
2. start the inner queued execution;
3. renew the lease at a configured interval while execution is active;
4. release a still-live lease after normal completion or a propagated inner failure;
5. if heartbeat renewal fails, cooperatively cancel the inner asyncio task and leave the lease to
   become `EXPIRED`.

The heartbeat interval must be strictly shorter than the lease duration.

The existing runtime already offloads deterministic verification and ContextPacket construction via
`asyncio.to_thread`, so long Docker verification does not intentionally block the heartbeat loop.

Cooperative cancellation is still not fencing. Synchronous Git work already in progress, another
thread, a stuck process, or a stale worker process may continue writing after heartbeat loss.

## Production worker identity

`DEVFLOW_WORKER_ID` may configure an explicit owner label. If omitted, DevFlow creates a value that is
stable for the lifetime of the worker process and changes after process restart.

Worker identity is auditable metadata, not a credential and not an authorization token.

## PostgreSQL boundary

Step 3.6 adds migration `0002_task_lease_heartbeat` and an index on `lease_until` for abandoned-work
queries.

`PostgresTaskLeaseStore` owns lease operations independently from `PostgresEvidenceStore`:

```text
PostgresTaskLeaseStore
  acquire_task_lease
  renew_task_lease
  release_task_lease
  inspect_task_lease
  list_task_leases
  list_expired_task_leases
```

Row locking serializes conflicting ownership changes. Run state and task identity are revalidated
inside the lease transaction.

Redis does not store or arbitrate ownership truth.

## Explicit Step 3.7 boundary

Step 3.6 intentionally leaves existing Git and PostgreSQL evidence write APIs unfenced.

A regression test proves the boundary directly: after a lease expires, an old caller can still use
the existing `PostgresEvidenceStore.append_evidence()` API while the Run remains `RUNNING`.

That behavior is **not accepted as safe takeover**. It is explicit evidence that Step 3.6 only adds
ownership/liveness observation.

Step 3.7 must add a fresh ownership generation (`run_token`) and require it at actual mutable write
boundaries before expired ownership can be transferred safely.

## Non-goals

Step 3.6 does not add:

- `run_token`;
- stale-writer fencing;
- expired-owner takeover/reassignment;
- exactly-once execution;
- automatic worker-death recovery;
- queue-owned scheduling;
- new Agent behavior;
- AST/RAG/vector retrieval changes;
- frontend/SSE behavior;
- structured event/log streaming beyond the lease snapshot itself.

Frozen principle:

> **Lease expiry can prove that ownership is no longer live from the coordinator's point of view; it
> cannot prove that the old process has lost the technical ability to write.**
