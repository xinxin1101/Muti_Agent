# DevFlow V1.1 Roadmap — Durable Agent Runtime

## Status

- Version line: **V1.1 — ACCEPTED / COMPLETE**
- Phase: **Phase 5 — Durable Agent Runtime — ACCEPTED / COMPLETE**
- Final step: **Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance — ACCEPTED / COMPLETE**
- V1.0 status: **ACCEPTED / COMPLETE**

Frozen V1.1 principle:

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

## Accepted Phase 5 capability ledger

| Step | Capability | Status | Frozen boundary |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | classify durable facts; no enqueue or mutation |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL records REQUESTED/ENQUEUED/PUBLISH_FAILED without pretending to be atomic with Redis |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked authority before recovery publication |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | reconstruct frontier from persisted DAG/evidence; no second scheduler truth |
| 5.5 | Durable Human Pause / Resume | **ACCEPTED / COMPLETE via Phase 6** | Human decisions survive restart and cannot bypass deterministic gates |
| 5.6 | Causal Trace Correlation | **ACCEPTED / COMPLETE** | trace explains history; never authorizes mutation |
| 5.7 | Operator Recovery / Approval Surface | **ACCEPTED / COMPLETE** | opaque action request + fresh server-side revalidation |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **ACCEPTED / COMPLETE** | deterministic failure injection proves existing authority; benchmark is never runtime truth |

## Final V1.1 architecture

```text
PostgreSQL typed/hash-validated Run + Task + DAG evidence
        +
durable dispatch-attempt ledger
        +
DB-time lease / heartbeat / monotonic generation
        +
run_token stale-write and Git fencing
        +
terminal worker evidence
        +
restart-safe DAG reconciliation
        +
durable Human Gate / bounded repair
        +
metadata-only causal trace
        +
opaque Operator ADVANCE_RUN request
        ↓
fresh server-side revalidation before every recovery mutation
        ↓
Step 5.8 deterministic chaos proof
```

Corollaries now accepted:

1. Redis is transport state, not recovery truth.
2. Worker absence is not replay authority.
3. Stale generations never regain evidence or Git write authority.
4. Persisted terminal worker/repair evidence is resumed from rather than recomputed by default.
5. Recovery mutation requires fresh PostgreSQL/Git/lease/dispatch/fencing facts.
6. Human approval is durable but cannot bypass verification, conflict binding, Git parentage or fencing.
7. Trace, metrics, benchmark output and browser state remain projections rather than authority.
8. Operator intent is only a request; Step 5.4/5.3/controller authority decides whether work may advance.

## Step 5.8 final proof

Manifest: `benchmarks/v1_1/chaos-recovery.json`

- schema version: `1`
- suite version: `0.2.0`
- suite SHA-256: `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`
- fault domains: **10**
- frozen invariants: **7**

Fault matrix:

```text
C01 ACTIVE worker loss
C02 stale evidence write
C03 stale Git mutation
C04 terminal evidence / controller crash
C05 duplicate reconciler race
C06 broker publish failure
C07 pending Human Gate restart
C08 DAG resume without completed-work rerun
C09 concurrent Operator ADVANCE_RUN race
C10 repair persisted / crash before integration Git CAS
```

Implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

- Backend Quality #956 (`32559638164`): **PASS** — **5/5 demos**, **10/10 chaos**, **442 passed**.
- Frontend Quality #255 (`32559638135`): **PASS**.

Complete candidate-ledger head:

`9c753404d48c577d7ec820d7e5bdea3ceb73df80`

- Backend Quality #961 (`32559859061`): **PASS**.
- Frontend Quality #260 (`32559859065`): **PASS**.

Those two independent double-green layers authorize the final accepted-state transition. The accepted-state head must pass both workflows once more; that final external CI result completes the V1.1 acceptance protocol.

## Out of scope for accepted V1.1

- arbitrary self-modifying Agent policies;
- autonomous force-push/rebase repair;
- multi-region consensus;
- Kubernetes operator/controller implementation;
- distributed PostgreSQL/Redis transaction claims without an explicit outbox design;
- trace/queue depth/benchmark/model-confidence based success authority;
- silently retrying semantic failures under the label of crash recovery.
