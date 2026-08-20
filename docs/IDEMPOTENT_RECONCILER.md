# Step 5.3 — Idempotent Task Reconciler

Status: **DESIGN FROZEN / IMPLEMENTATION IN PROGRESS**

## Goal

Step 5.3 is the first V1.1 recovery phase allowed to mutate runtime state. It converts a recovery candidate into a fresh durable dispatch attempt only after re-reading and locking the current PostgreSQL authority.

A `RunRecoveryPlan` from Step 5.1 is diagnostic only. It is never passed back as mutation authorization.

Frozen principle:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

## Authority flow

```text
optional/stale recovery diagnosis
        ↓ never authoritative
TaskReconciler.reconcile(run_id, task_id)
        ↓
PostgresTaskReconciliationStore.prepare_task()
        ↓
BEGIN
  lock Run
  lock Task
  read DB time
  validate current lease state
  validate terminal WORKER_EXECUTION evidence
  validate durable dispatch-attempt history
  allocate at most one fresh REQUESTED attempt when safe
COMMIT
        ↓
only the caller that created that REQUESTED row may publish it
        ↓
Dramatiq actor.send(stable dispatch_id)
        ↓
ENQUEUED / PUBLISH_FAILED / unresolved REQUESTED crash window
        ↓
worker receives message
        ↓
existing PostgresTaskLeaseStore.acquire_task_lease()
        ↓
expired generation N → generation N+1 + fresh run_token
```

The reconciler does not directly mint a `run_token` or assign a worker owner. Lease generation remains owned by `PostgresTaskLeaseStore` when a worker actually enters the execution boundary.

## Safe mutation cases

### UNOWNED + no dispatch attempt

This proves no durable dispatcher intent has ever been committed for the Task. The locked reconciler may create attempt 1 as `REQUESTED`.

### EXPIRED current generation + no terminal execution evidence

The stale generation has lost liveness and its `run_token` can no longer authorize live writes. If no newer dispatch attempt already exists, the reconciler may create one fresh attempt with a new `dispatch_id` and the next monotonic attempt number.

The new worker message later acquires generation N+1 through the existing lease store.

## Non-mutation cases

### ACTIVE lease

Wait. Recovery must not race a live fenced owner.

### terminal Run

No action. Recovery cannot reopen a terminal Run.

### terminal WORKER_EXECUTION evidence for the current dispatch

Resume downstream from durable evidence. Do not rerun the task.

### REQUESTED attempt already exists beyond the current generation

Wait/block. The broker publication outcome may be unknown. A second reconciler must not call `actor.send()` for that existing attempt.

### ENQUEUED attempt already exists beyond the current generation

Wait for the worker to acquire ownership. Do not allocate another attempt.

### PUBLISH_FAILED attempt

Block automatic redispatch. Step 5.2 deliberately does not treat `BrokerConnectionError` as mathematical proof of broker non-delivery.

### RELEASED lease without terminal execution evidence

Remain blocked. Released ownership history is not silently reopened.

## Concurrency rule

All reconciliation preparation locks the Task row. Therefore two reconcilers racing the same Task linearize:

```text
R1 locks Task → creates attempt N+1 REQUESTED → commit
R2 locks Task → observes attempt N+1 → WAIT_EXISTING_DISPATCH
```

Only R1 receives `publish_allowed=True`. R2 never publishes.

If R1 crashes after COMMIT and before `actor.send()`, the attempt remains REQUESTED. This sacrifices liveness rather than guessing whether publication happened. A later V1.1 operator surface may expose that ambiguity, but Step 5.3 does not auto-republish it.

## Required fail-closed checks

- Run/Task identity must exist and agree.
- attempt numbers for a Task must be strictly contiguous from 1.
- dispatch IDs are immutable and unique.
- terminal worker evidence payload hash/type/Run/Task identity must validate.
- at most one terminal worker execution row may exist per dispatch.
- worker execution evidence must refer to a known durable dispatch attempt in the V1.1 recovery path.
- an ACTIVE lease never creates a recovery attempt.
- a current terminal worker execution never creates a recovery attempt.
- a newer REQUESTED/ENQUEUED/PUBLISH_FAILED attempt prevents another attempt allocation.
- a recovery attempt always uses a fresh `dispatch_id`.

## Explicit non-goals

Step 5.3 does not:

- inspect Redis queue contents as authority;
- claim exactly-once broker delivery;
- automatically retry an ambiguous REQUESTED attempt;
- automatically retry PUBLISH_FAILED;
- mutate Git/worktrees itself;
- advance DAG dependencies; that belongs to Step 5.4;
- implement durable Human Gate state; that belongs to Step 5.5;
- accept browser-supplied lease generation, `run_token`, dispatch history, or broker payload as authority.

## Acceptance target

Step 5.3 is accepted only after real PostgreSQL tests prove:

1. UNOWNED + no attempt creates exactly one attempt 1;
2. ACTIVE ownership never redispatches;
3. EXPIRED + no terminal evidence creates exactly one fresh recovery attempt;
4. concurrent reconcilers create at most one fresh attempt;
5. existing newer REQUESTED/ENQUEUED/PUBLISH_FAILED prevents a second attempt;
6. terminal worker evidence prevents rerun;
7. fresh recovery dispatch is published at most once by the creating reconciler call;
8. worker acquisition of the fresh dispatch advances lease generation and installs a fresh `run_token`;
9. the previous generation cannot write after takeover;
10. crash after durable prepare leaves REQUESTED rather than fabricating broker outcome.
