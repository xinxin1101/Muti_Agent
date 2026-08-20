# Step 5.3 Acceptance — Idempotent Task Reconciler

Status: **PENDING / NOT YET ACCEPTED**

This ledger exists before final acceptance so the acceptance edit itself is forced through both quality workflows. Do not treat this document as acceptance evidence until the status changes to `ACCEPTED / COMPLETE` after exact-head CI and merge review.

## Acceptance target

Step 5.3 is the first V1.1 recovery stage allowed to mutate runtime state. It must turn a recovery candidate into at most one new durable dispatch attempt only after fresh PostgreSQL revalidation under Run/Task locks.

Frozen boundary:

> **A recovery diagnosis may nominate work; only fresh locked PostgreSQL facts may authorize a new dispatch attempt.**

## Required linearization

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
hold locks across bounded broker send
        ↓
ENQUEUED / PUBLISH_FAILED
COMMIT
```

If the process fails after broker acceptance but before Txn B can commit its observed outcome, Txn B rolls back while the earlier `REQUESTED` remains durable. Recovery must preserve that ambiguity rather than guess failure or implicitly republish.

## Required evidence before acceptance

- fresh locked revalidation immediately before broker publication: implemented, pending CI;
- concurrent reconcilers create/publish at most one dispatch: implemented test, pending CI;
- ACTIVE generation produces no redispatch: implemented test, pending CI;
- EXPIRED generation gets one fresh dispatch identity only: implemented test, pending CI;
- recovered worker acquisition advances lease generation N → N+1: implemented test, pending CI;
- fresh generation receives a fresh `run_token`: implemented test, pending CI;
- prior generation `run_token` remains fenced after takeover: implemented test, pending CI;
- REQUESTED/PUBLISH_FAILED histories are never implicitly republished: implementation boundary present, pending review/CI;
- terminal worker evidence is resumed rather than rerun: implementation boundary present, pending review/CI;
- existing 5/5 deterministic V1 demos remain green: pending;
- full backend pytest: pending;
- Frontend Quality: pending;
- merge review / unresolved threads: pending;
- exact implementation head: pending;
- final acceptance/progress ledger head: pending.

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
