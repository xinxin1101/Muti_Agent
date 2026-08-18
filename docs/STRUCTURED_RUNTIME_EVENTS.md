# Structured Runtime Events — Phase 3 / Step 3.8

## Purpose

Step 3.8 adds a compact, queryable, append-only event timeline over DevFlow's already accepted
runtime facts.

The event timeline is **not** a second success authority and it is **not** a replacement for typed
runtime evidence. Its job is correlation and observability:

```text
Run / Task / Dispatch / Generation
        ↓
accepted persistence fact
        ↓
same PostgreSQL transaction
        ↓
structured runtime event
        ↓
monotonic per-Run sequence
        ↓
queryable timeline
```

Frozen principle:

> **Typed evidence decides what happened; structured events make that history queryable.**

## Source-of-truth boundaries

- Git/worktrees remain repository and code-state truth.
- deterministic verification + independent Reviewer + validated Git evidence remain task-success
  authority.
- PostgreSQL typed evidence remains durable runtime evidence.
- Redis/Dramatiq remains dispatch transport.
- `run_token` remains a write-fencing capability only.
- `runtime_events` is an observability/audit projection over accepted facts.

An event row must never be interpreted as proof that a task succeeded merely because its message says
so.

## Event shape

Every persisted event contains:

```text
id                    database row identity
event_id              globally unique UUID
run_id                required Run correlation
sequence              monotonic sequence within one Run
event_key             idempotency key within one Run
kind                   stable event category
source                 subsystem that produced the accepted fact
level                  INFO / WARNING / ERROR
task_id                optional task correlation
dispatch_id            optional dispatch correlation
generation             optional fenced lease generation
message                compact human-readable summary
schema_version         event schema version
attributes             bounded JSON metadata
attributes_sha256      integrity hash
created_at             PostgreSQL timestamp
```

`RuntimeEventDraft` rejects structured sensitive attributes such as `run_token`, API keys,
Authorization values, passwords, access/refresh tokens, and secrets. Attributes are limited to 16 KiB
of canonical JSON so the timeline cannot silently become a second large evidence/blob store.

## Ordering and idempotency

`runs.event_sequence` is incremented while the Run row is locked. Every event therefore receives a
strictly increasing sequence for its Run.

Same-Run event writes are serialized through the existing PostgreSQL Run lock. This gives a stable
observable order even when independent tasks are being processed concurrently.

`(run_id, event_key)` is the idempotency boundary. Reusing the same key with identical structured data
returns the existing event. Reusing the key for different data fails closed.

The event sequence is an audit ordering primitive, not an exactly-once execution claim.

## Transactional projection

Step 3.8 avoids a dual-write gap by creating events inside the transaction that accepts the
corresponding durable fact.

### Run lifecycle

`PostgresEvidenceStore.start_run()` writes `RUN_STARTED` after the Run and Task rows exist.

`finalize_single_task_run()` writes `RUN_FINALIZED` in the same transaction that writes terminal Run
status/result evidence.

### Typed evidence

Every newly accepted `EvidenceRow` produces one `EVIDENCE_RECORDED` projection in the same transaction.
The event does not duplicate the evidence payload. It stores only correlation and compact metadata:

```text
evidence_id
evidence_key
evidence_kind
stage
evidence_sequence
payload_sha256
```

Event `source` makes the timeline filterable across runtime subsystems:

- `DEVELOPER_RUN` → `AGENT`
- `VERIFICATION_RESULT` → `VERIFICATION`
- `REVIEW_DECISION` → `REVIEW`
- `REPAIR_RUN` → `REPAIR`
- `DISPATCH_EVENT` → `DISPATCH`
- `WORKER_EXECUTION` → `WORKER`
- merge/integration evidence → `INTEGRATION`
- state/failure/context evidence → `RUNTIME`

### Lease / generation lifecycle

`PostgresTaskLeaseStore` writes lease events in the same transaction as ownership mutation:

- `LEASE_ACQUIRED`
- `LEASE_TAKEN_OVER`
- `LEASE_HEARTBEAT`
- `LEASE_RELEASED`

These events include `task_id`, `dispatch_id`, and the durable numeric `generation`, but never the
`run_token`.

This matters for terminal cleanup: the outer leased worker may release a still-live generation after a
single-task Run has already finalized. Because the release event is written by the lease transaction,
the timeline can still record that cleanup without reopening typed evidence writes.

## Query boundary

`PostgresEvidenceStore.list_runtime_events()` provides a bounded query surface with:

- `task_id`
- `dispatch_id`
- `kind`
- `source`
- `after_sequence`
- `limit` (1–1000)

This is intentionally suitable for later cursor/SSE work without implementing SSE in Step 3.8.

## Migration behavior

Migration `0004_structured_runtime_events`:

1. adds `runs.event_sequence`;
2. creates `runtime_events` with sequence/idempotency/integrity constraints and indexes;
3. does **not** synthesize historical events for rows written before Step 3.8.

Old evidence remains authoritative and queryable through its existing APIs. The structured timeline
begins from facts accepted after the migration.

## Explicitly out of scope

Step 3.8 does not add:

- frontend run dashboards;
- SSE/WebSocket streaming;
- external log aggregation or OpenTelemetry collectors;
- new scheduler/recovery behavior;
- automatic worker-death redispatch;
- new Agent/RAG/AST behavior;
- exactly-once execution semantics;
- a second copy of raw model prompts, outputs, verification stdout/stderr, or source-code context.
