# Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance

Status: **ACCEPTED / COMPLETE**

## Frozen boundary

> **Chaos may perturb delivery, liveness, timing, process continuity, and stale actors; it may not weaken the production authority model or replace production stores with an easier test-only truth.**

Step 5.8 adds no recovery mechanism. It proves the accepted Steps 5.1–5.7 authority model under deterministic crash, race, stale-owner, broker, Git, Human Gate, repair, DAG and Operator failure injection.

## Accepted benchmark

Manifest: `benchmarks/v1_1/chaos-recovery.json`

- schema version: `1`
- suite version: `0.2.0`
- suite SHA-256: `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`
- required fault domains: **10**
- frozen invariants: **7**

Required scenarios:

| ID | Fault boundary | Accepted proof |
| --- | --- | --- |
| C01 | ACTIVE worker disappears | no takeover/publication before durable lease expiry |
| C02 | stale evidence write | old `run_token` is rejected and accepted evidence remains unchanged |
| C03 | stale Git mutation | expired generation cannot mutate authoritative Git state |
| C04 | terminal evidence then controller crash | accepted terminal work resumes from evidence and is not rerun |
| C05 | duplicate reconciler race | at most one fresh recovery publication wins |
| C06 | broker publish failure after durable intent | `PUBLISH_FAILED` remains explicit; execution/success is not invented |
| C07 | Human Gate pending across restart | exact pending gate/fingerprint is reconstructed from durable evidence |
| C08 | DAG restart after completed dependency | completed work is not rerun; only the legal frontier advances |
| C09 | concurrent Operator `ADVANCE_RUN` | fresh revalidation yields at most one publication; old action becomes stale |
| C10 | repair persisted, crash before Git CAS | exact staged repair commit survives and is reused without rerunning Agent; integration advances only through accepted CAS |

Frozen invariants:

- `AT_MOST_ONE_MUTATION`
- `STALE_GENERATION_FENCED`
- `UNKNOWN_STATE_NOT_GUESSED`
- `FAILURE_NOT_SUCCESS`
- `COMPLETED_WORK_NOT_RERUN`
- `HUMAN_DECISION_DURABLE`
- `OBSERVABILITY_NOT_AUTHORITY`

## Harness authority

The chaos runner may validate the versioned manifest, select exact bounded pytest node IDs, enforce timeout/identity rules, execute deterministic scenarios, and emit a structured result.

It may not:

- become scheduler/recovery/success authority;
- manufacture `run_token`, generation, dispatch identity, Git SHA or Human/Operator authority;
- treat Redis/queue absence as truth;
- weaken PostgreSQL locking, lease/fencing, Git parent/CAS or verification rules;
- let approval mean “merge anyway”;
- let persisted repair evidence mean integration succeeded before Git CAS;
- silently skip a required scenario or aggregate a partial pass into acceptance.

## CI evidence

Implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Backend Quality #956 (`32559638164`): **PASS** — Alembic round trip, verifier image, Ruff, V1 fixtures, **5/5 demos**, **10/10 chaos**, **442 passed, 1 warning**.

Frontend Quality #255 (`32559638135`): **PASS**.

Complete candidate-ledger head:

`9c753404d48c577d7ec820d7e5bdea3ceb73df80`

Backend Quality #961 (`32559859061`): **PASS**.

Frontend Quality #260 (`32559859065`): **PASS**.

Because the implementation/hardening head and the complete candidate ledger both independently passed strict Backend + Frontend quality gates, the repository is allowed to transition Step 5.8, Phase 5 and V1.1 to `ACCEPTED / COMPLETE`.

The accepted-state ledger head created by this transition must itself pass Backend + Frontend Quality once more. That final external CI result validates this already-written accepted state; no self-referential follow-up edit is required merely to copy its own run IDs back into this file.
