# Step 5.2 Acceptance — Durable Dispatch Attempt Ledger

Status: **ACCEPTED / COMPLETE**

## Accepted capability

Step 5.2 adds a PostgreSQL-backed dispatch-attempt ledger that durably records dispatcher intent before broker publication and records only publication outcomes that the process actually observed.

Accepted publication states:

```text
REQUESTED
    ├─ broker acknowledgement observed → ENQUEUED
    └─ bounded BrokerConnectionError observed → PUBLISH_FAILED
```

The ledger does **not** claim an atomic transaction or exactly-once delivery across PostgreSQL and Redis/Dramatiq.

A process crash after `actor.send()` but before `mark_enqueued()` leaves the attempt in `REQUESTED`. That state means the broker publication outcome is unknown. It is never silently rewritten as failure and never authorizes an implicit second publication.

Frozen boundary:

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

## Accepted architecture

```text
persisted RUNNING Run + Task
        ↓
PostgresDispatchAttemptStore.begin_initial_attempt()
        ↓
REQUESTED committed with stable dispatch_id
        ↓
DurableDramatiqTaskDispatcher
        ↓
actor.send(minimal envelope)
        ↓
┌────────────────────────────┬──────────────────────────────┐
│ broker acknowledgement     │ BrokerConnectionError        │
│ observed                   │ observed                     │
↓                            ↓
ENQUEUED                     PUBLISH_FAILED
```

Production Product API composition now uses `DurableDramatiqTaskDispatcher`; the earlier `DramatiqTaskDispatcher` remains a lower-level transport adapter/test boundary rather than the production dispatch entry point.

## Durable invariants

- `dispatch_id` is the stable identity of one publication attempt.
- ordinary dispatch may create only `attempt_number = 1`; recovery attempts are reserved for Step 5.3.
- a `dispatch_id` cannot be rebound to another Run or Task.
- `(run_id, task_id, attempt_number)` is unique.
- a Task row is locked before initial attempt creation, serializing competing initial dispatchers.
- `REQUESTED` contains no broker acknowledgement or failure facts.
- `ENQUEUED` requires immutable `broker_message_id` and `queue_name`.
- `PUBLISH_FAILED` requires bounded error facts and contains no broker acknowledgement facts.
- `ENQUEUED` and `PUBLISH_FAILED` are terminal observations for that attempt and cannot rewrite each other.
- exact terminal-state replays are idempotent; conflicting replays fail closed.
- replay of an already-`ENQUEUED` dispatch reconstructs the receipt from PostgreSQL without calling the broker again.
- replay of `REQUESTED` or `PUBLISH_FAILED` is rejected before broker publication; recovery must decide any later action.
- a different `dispatch_id` for a Task that already has an attempt is rejected by the ordinary dispatcher path.
- `BrokerConnectionError` is an observed publication failure, **not proof of broker non-delivery**; it does not authorize automatic republish.
- unexpected failure after a possible `actor.send()` acceptance naturally preserves `REQUESTED` and therefore the dual-write ambiguity.

## Migration evidence

Step 5.2 adds:

`backend/alembic/versions/0007_dispatch_attempts.py`

Exact implementation-head CI proved:

```text
0001 → 0007
0007 → base
base → 0007
```

all succeed on PostgreSQL 16.

The schema includes state, shape, identity, foreign-key, uniqueness, and lookup-index constraints matching the typed persistence model.

## Failure-path evidence

Real PostgreSQL tests prove:

1. initial `REQUESTED` creation and same-dispatch idempotency;
2. rejection of a second independent initial attempt for the same Task;
3. monotonic `REQUESTED → ENQUEUED`;
4. immutable broker acknowledgement facts;
5. monotonic `REQUESTED → PUBLISH_FAILED`;
6. immutable bounded failure facts;
7. terminal publication states cannot rewrite one another;
8. a dispatch identity cannot change Run/Task correlation;
9. a simulated process failure after durable `REQUESTED` leaves the row unresolved rather than manufacturing failure;
10. a real dispatcher `BrokerConnectionError` becomes durable `PUBLISH_FAILED` and a repeated call does not invoke `actor.send()` again;
11. an already-`ENQUEUED` attempt returns the persisted receipt without a second broker call.

## Implementation-head acceptance evidence

Exact implementation head:

`30e92051aeae00e674010b66ecb8da329c793e0a`

Backend Quality:

- PostgreSQL + Redis services: **PASS**;
- Alembic upgrade → downgrade base → re-upgrade through `0007`: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **380 passed in 28.15s**.

Frontend Quality on the same implementation head:

- locked install: **PASS**;
- TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **PASS**;
- Vite production build: **PASS**.

PR #35 merge review found no unresolved review threads. The implementation review also confirmed that all persisted re-entry paths avoid hidden broker republish and that successful broker publication followed by a failed PostgreSQL acknowledgement remains an unresolved `REQUESTED` attempt.

The acceptance/progress ledger paths themselves are gated by both Backend Quality and Frontend Quality. This acceptance is mergeable only after the final ledger head independently passes both workflows.

## Explicit non-goals

Step 5.2 does not:

- allocate recovery attempt number 2+;
- decide whether an unresolved or failed publication may be retried;
- acquire/take over/renew/release task leases;
- allocate a new `run_token` generation;
- reconcile stale workers;
- advance DAG scheduling;
- infer broker delivery from Redis inspection;
- claim exactly-once delivery;
- use browser state as dispatch authority.

Those mutation decisions begin in Step 5.3 and must use fresh locked PostgreSQL revalidation.

## Next step

**Step 5.3 — Idempotent Task Reconciler**

Step 5.3 is the first recovery phase allowed to mutate runtime state. A stale recovery plan or a Step 5.1 `REDISPATCH_CANDIDATE` is never sufficient authorization by itself.
