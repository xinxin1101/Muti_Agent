# Step 5.2 Acceptance — Durable Dispatch Attempt Ledger

Status: **PENDING / NOT YET ACCEPTED**

This ledger is intentionally present before final acceptance so both Backend Quality and Frontend Quality path gates can prove the final acceptance edit itself. Do not treat this file as acceptance evidence until the status is changed to `ACCEPTED / COMPLETE` after exact-head CI and merge review.

## Acceptance target

Step 5.2 must establish a durable PostgreSQL record of each dispatcher publication attempt without claiming an atomic transaction or exactly-once delivery across PostgreSQL and Redis/Dramatiq.

Required publication states:

```text
REQUESTED
    ├─ broker acknowledgement observed → ENQUEUED
    └─ bounded BrokerConnectionError observed → PUBLISH_FAILED
```

A process crash after `actor.send()` but before the acknowledgement can be persisted must leave the durable attempt in `REQUESTED`. That state means the broker publication outcome is unknown. It must not be rewritten as failure and must not trigger an implicit second publication.

Frozen boundary:

> **PostgreSQL records dispatch intent and observed publication outcomes; it never pretends to be an atomic transaction with Redis.**

## Required evidence before acceptance

- Alembic `0001 → 0007 → base → 0007` round-trip: pending.
- Ruff: pending.
- deterministic V1 control-plane demos: pending.
- full backend pytest: pending.
- Frontend Quality: pending.
- crash-window integration proof: implemented, pending CI.
- known broker failure without implicit republish: implemented, pending CI.
- merge review: pending.
- exact implementation head: pending.
- final ledger head: pending.
