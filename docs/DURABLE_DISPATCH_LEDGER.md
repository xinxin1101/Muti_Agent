# Phase 5 / Step 5.2 — Durable Dispatch Attempt Ledger

## Status

**IN PROGRESS**

- Version line: **V1.1**
- Phase: **Phase 5 — Durable Agent Runtime**
- Baseline: `54bdc1f97333170424fecf6911e95d84fa7c2d04`
- Previous step: **5.1 Recovery State Classifier — ACCEPTED / COMPLETE**
- Current step: **5.2 Durable Dispatch Attempt Ledger**
- Next step after acceptance: **5.3 Idempotent Task Reconciler**

## Problem

V1.0 validates Run/Task identity and then calls Dramatiq directly:

```text
read RUNNING Run
        ↓
create dispatch_id
        ↓
actor.send(...)
```

If the process dies around `actor.send()`, PostgreSQL cannot answer whether the task was never dispatched, a publish was attempted, or the broker acknowledged the message before the process lost its durable bookkeeping opportunity.

Step 5.1 therefore correctly treats an `UNOWNED` task as ambiguous.

## Frozen principle

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

A broker acknowledgement is a transport observation, not execution success. A client-side publish failure is not proof that Redis never received a message.

## Accepted target architecture

```text
validated RUNNING Run + Task
        ↓
PostgreSQL: create REQUESTED dispatch attempt
        ↓ commit
Dramatiq actor.send(stable dispatch_id)
        ↓
        ├── acknowledgement observed
        │       ↓
        │   PostgreSQL: ENQUEUED
        │
        ├── BrokerConnectionError observed
        │       ↓
        │   PostgreSQL: PUBLISH_FAILED
        │
        └── process dies before durable resolution
                ↓
            REQUESTED remains
            publication outcome = UNKNOWN
```

There is deliberately no distributed transaction spanning PostgreSQL and Redis.

## Durable state machine

```text
REQUESTED
   ├──> ENQUEUED
   └──> PUBLISH_FAILED
```

Transitions are monotonic. `ENQUEUED` and `PUBLISH_FAILED` are terminal publication observations for that dispatch attempt in Step 5.2.

### REQUESTED

PostgreSQL proves:

- a specific `dispatch_id` was allocated;
- it is bound to exactly one `(run_id, task_id)`;
- the Run and Task were valid at the durable request boundary;
- publication may or may not have happened afterward.

It does **not** prove that Redis has or has not accepted the message.

### ENQUEUED

The dispatcher observed a Dramatiq broker acknowledgement and durably stored the returned message id and queue name.

It does **not** prove that a worker received, started, or completed the task.

### PUBLISH_FAILED

The dispatcher observed a bounded broker publication error and durably recorded that observation.

It does **not** prove non-delivery. Network/broker failures can occur around acknowledgement boundaries, so recovery must not allocate a fresh execution generation merely because this state exists.

## Stable dispatch identity

The queue payload remains exactly:

```text
dispatch_id
run_id
task_id
```

No credential, repository content, `run_token`, prompt, or recovery authority is added to Redis.

The existing lease semantics make stable dispatch identity valuable:

- duplicate delivery of the same dispatch while one owner is ACTIVE cannot obtain a second lease;
- an expired generation cannot be resurrected by the same `dispatch_id`;
- takeover after expiry requires a fresh dispatch identity and therefore belongs to Step 5.3.

This provides at-least-once transport tolerance without claiming exactly-once delivery.

## Initial-dispatch duplication boundary

Step 5.2 prevents the normal dispatcher from silently allocating a second independent dispatch attempt for the same task after a durable attempt already exists.

A future recovery attempt must be created only through Step 5.3 after fresh locked recovery revalidation.

This avoids:

```text
HTTP retry / process retry
        ↓
new dispatch_id
        ↓
multiple independently valid broker messages
```

## Persistence shape

A dedicated `dispatch_attempts` table records bounded scalar facts:

```text
dispatch_id              UUID primary key
run_id                   UUID
 task_id                  text
attempt_number           integer >= 1
state                    REQUESTED | ENQUEUED | PUBLISH_FAILED
broker_message_id        nullable bounded text
queue_name               nullable bounded text
error_code               nullable bounded text
error_message            nullable bounded text
requested_at             database timestamp
resolved_at              nullable database timestamp
updated_at               database timestamp
```

The schema enforces state-dependent field shapes and task/run foreign-key identity.

Step 5.2 creates only attempt number `1` through the ordinary dispatcher. The table supports later attempt numbers, but only Step 5.3 may authorize creating them after recovery checks.

## Idempotency rules

Reusing the same `dispatch_id` for the same Run/Task returns the existing durable attempt.

Reusing a `dispatch_id` for different identity fails closed.

Replaying a completed `ENQUEUED` attempt does not call the broker again; the persisted receipt is returned.

A `REQUESTED` or `PUBLISH_FAILED` attempt is not automatically republished by Step 5.2. It is surfaced for recovery rather than guessed into a new broker action.

## Event projection

Durable ledger transitions may emit compact runtime events:

```text
DISPATCH_REQUESTED
DISPATCH_ENQUEUED
DISPATCH_PUBLISH_FAILED
```

Events are observability projections only. The `dispatch_attempts` row is the typed durable publication state, and neither can decide task success.

## Crash windows

### Crash before REQUESTED commit

No durable dispatch attempt exists and no broker call has occurred through the Step 5.2 dispatcher.

### Crash after REQUESTED commit but before `actor.send()`

State remains `REQUESTED`. Publication outcome is unknown to recovery; Step 5.2 does not guess.

### Crash during/after broker publication before ENQUEUED commit

State remains `REQUESTED` even if Redis may have accepted the message. This is the critical dual-write ambiguity that the design preserves explicitly.

### Broker error followed by persistence error

The attempt may remain `REQUESTED`; the dispatcher must not forge `PUBLISH_FAILED` if that transition was not durably committed.

### Crash after ENQUEUED commit

PostgreSQL proves broker acknowledgement was observed. Worker liveness is still determined by lease/heartbeat and worker evidence.

## Security / privacy boundary

The ledger must never store:

- `run_token`;
- API keys or credentials;
- repository/worktree contents;
- raw prompts/completions;
- arbitrary queue payloads;
- access tokens;
- broker connection URLs.

## Step 5.2 acceptance requirements

1. migration upgrade/downgrade is deterministic;
2. REQUESTED is committed before the broker call;
3. successful broker acknowledgement becomes ENQUEUED;
4. observed broker failure becomes PUBLISH_FAILED;
5. crash-window simulation preserves REQUESTED rather than inventing an outcome;
6. same-dispatch idempotency never republishes an already ENQUEUED message;
7. dispatch id reuse across different Run/Task identity fails closed;
8. the ordinary dispatcher cannot create a second independent attempt for a task;
9. queue payload remains minimal and contains no fencing capability;
10. existing lease/run_token safety tests remain green;
11. existing 5/5 deterministic V1 demos remain green;
12. full Backend Quality is green on the exact implementation head.

## Explicitly deferred to Step 5.3

Step 5.2 does not:

- automatically republish REQUESTED/PUBLISH_FAILED attempts;
- allocate a fresh dispatch after lease expiry;
- acquire a new lease generation;
- advance DAG scheduling;
- infer task success from broker state;
- convert publication state into runtime authorization.

Step 5.3 will combine this ledger with Step 5.1 recovery classification and fresh PostgreSQL locks to perform bounded reconciliation.

## Final boundary

> **Step 5.2 makes dispatch intent and publication observations durable; it does not make broker delivery exactly-once and it does not authorize recovery execution.**
