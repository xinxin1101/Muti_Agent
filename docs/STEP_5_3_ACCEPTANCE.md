# Step 5.3 Acceptance — Idempotent Task Reconciler

Status: **ACCEPTED / COMPLETE**

Step 5.3 is the first V1.1 recovery stage allowed to mutate runtime state. It converts a recovery candidate into at most one new durable dispatch attempt only after fresh PostgreSQL revalidation under Run/Task locks.

Frozen boundary:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

## Accepted linearization

```text
Txn A
lock Run + Task
fresh DB time
validate lease/evidence/dispatch history
prepare REQUESTED
COMMIT
        ↓
Txn B
re-lock Run + Task + latest attempt
fresh DB time
revalidate authority
hold locks across broker send
        ↓
ENQUEUED / PUBLISH_FAILED
COMMIT
```

If the process fails after broker acceptance but before Txn B can commit its observed outcome, Txn B rolls back while the earlier `REQUESTED` remains durable. Recovery preserves that ambiguity rather than guessing failure or implicitly republishing.

The reconciler does not directly advance lease generation and does not create a `run_token`. A worker that consumes an accepted fresh dispatch still enters the existing `PostgresTaskLeaseStore.acquire_task_lease()` authority, which performs the generation N → N+1 takeover and creates a fresh fenced token.

## Accepted recovery guarantees

- `prepare_task()` locks Run/Task authority and uses fresh database time before allocating a dispatch attempt;
- `guard_prepared_publication()` re-locks and revalidates immediately before broker publication, closing the prepare/send TOCTOU window;
- Run, Task, and latest dispatch-attempt authority remain locked across the bounded broker publication window;
- concurrent reconcilers create/publish at most one fresh dispatch for the same Task;
- ACTIVE generations are never redispatched;
- EXPIRED generations without terminal worker evidence receive at most one fresh dispatch identity;
- recovered worker acquisition advances generation N → N+1 through the existing lease authority;
- the recovered generation receives a fresh `run_token`;
- the prior generation's `run_token` remains fenced after takeover;
- terminal `WORKER_EXECUTION` evidence is resumed rather than rerun;
- existing `REQUESTED` and `PUBLISH_FAILED` histories are never implicitly republished;
- worker-side evidence is hash-validated, typed, and correlated to a durable dispatch identity before it influences recovery;
- malformed lease, dispatch, or worker-evidence history fails closed.

## PostgreSQL regression evidence

The Step 5.3 integration suite proves:

1. two concurrent reconcilers over an UNOWNED Task result in one broker call and one attempt;
2. an ACTIVE owner produces `WAIT_ACTIVE_OWNER` and zero broker calls;
3. an EXPIRED generation produces one recovery attempt even under concurrent reconcilers;
4. a worker consuming that recovery attempt acquires generation N+1 with a fresh `run_token`;
5. the old token is rejected by the existing Git-mutation fencing guard;
6. accepted terminal worker evidence produces `RESUME_TERMINAL_EVIDENCE` with no rerun;
7. an observed `PUBLISH_FAILED` attempt remains blocked on later reconciliation and is not sent again.

The expired-generation fault injection was hardened to preserve the valid lease timeline invariant:

```text
lease_acquired_at < heartbeat_at < lease_until < observed_at
```

No production lease validation was relaxed.

## Implementation-head quality evidence

Exact implementation head:

`0d7586405853f19923b906b782ac6ab167886ffe`

Backend Quality:

- PostgreSQL + Redis services: **PASS**;
- Alembic `0001 → 0007 → base → 0007`: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **385 passed in 35.08s**.

Frontend Quality on the same implementation head:

- locked install: **PASS**;
- TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **PASS**;
- Vite production build: **PASS**.

PR #36 merge review at implementation acceptance found **0 unresolved review threads**.

The acceptance/progress ledger paths themselves trigger both Backend Quality and Frontend Quality. The final ledger head must independently pass both workflows before merge.

## Explicit non-authority

Step 5.3 does not:

- derive success from recovery activity;
- reopen terminal Runs;
- reuse an expired `dispatch_id`;
- reuse an expired `run_token`;
- let the browser choose dispatch identity or lease generation;
- directly increment generation outside the existing worker lease-acquisition authority;
- infer broker non-delivery from a missing acknowledgement;
- create DAG-wide scheduling truth; that belongs to Step 5.4.

## Next authority gap

Step 5.3 can safely reconcile one Task. Step 5.4 must reconstruct a whole persisted DAG frontier without becoming a second scheduler truth:

```text
validated persisted DAG
        +
accepted terminal task evidence
        +
fresh ownership/recovery facts
        ↓
DAG-wide Run reconciliation
```

Completed dependencies must never rerun, downstream work must not become READY before all accepted dependencies succeed, and every actual dispatch remains delegated to the Step 5.3 per-Task authority.
