# DevFlow V1.1 Roadmap — Durable Agent Runtime

## Status

- Version line: **V1.1**
- Phase: **Phase 5 — Durable Agent Runtime**
- Current step: **Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance — ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**
- Steps 5.1–5.7: **ACCEPTED / COMPLETE**
- Step 5.8 implementation/hardening: **COMPLETE, awaiting candidate-ledger and accepted-state CI**
- V1.0 status: **ACCEPTED / COMPLETE**
- V1.1 status: **IN PROGRESS / NOT YET ACCEPTED**

## Why V1.1 exists

V1.0 proved that DevFlow can safely execute, verify, review, integrate, publish, observe, and evaluate software-engineering work. V1.1 closes the next production gap: durable execution across process failure, worker loss, delayed messages, long waits, human intervention, and operator recovery.

Frozen V1.1 principle:

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

Corollaries:

1. Redis queue contents are transport state, not recovery authority.
2. Absence of a worker process is not proof that work may be replayed.
3. A stale worker generation never regains evidence or Git write authority.
4. Persisted terminal worker/repair evidence is resumed from rather than recomputed by default.
5. Mutating recovery decisions receive fresh PostgreSQL/Git/lease/dispatch/fencing revalidation.
6. Human approval is durable authority but cannot bypass verification, Git parentage, conflict binding, or fencing.
7. Causal Trace, metrics, benchmark results, and browser state remain projections rather than success/mutation authority.
8. Operator intent is only a request; existing reconciler/controller authority decides whether it is still legal.

---

# Phase 5 capability ledger

| Step | Capability | Status | Frozen boundary |
| --- | --- | --- | --- |
| 5.1 | Recovery State Classifier | **ACCEPTED / COMPLETE** | classify durable recovery facts; no enqueue or mutation |
| 5.2 | Durable Dispatch Attempt Ledger | **ACCEPTED / COMPLETE** | PostgreSQL records REQUESTED/ENQUEUED/PUBLISH_FAILED without pretending to be atomic with Redis |
| 5.3 | Idempotent Task Reconciler | **ACCEPTED / COMPLETE** | fresh locked authority before any recovery publication |
| 5.4 | DAG-wide Run Reconciliation | **ACCEPTED / COMPLETE** | reconstruct frontier from persisted DAG/evidence; no second scheduler truth |
| 5.5 | Durable Human Pause / Resume | **ACCEPTED / COMPLETE via Phase 6** | decisions survive restart; approval never bypasses deterministic gates |
| 5.6 | Causal Trace Correlation | **ACCEPTED / COMPLETE** | trace explains accepted history; never authorizes mutation |
| 5.7 | Operator Recovery / Approval Surface | **ACCEPTED / COMPLETE** | browser sends opaque server action only; fresh facts decide mutation |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED** | inject failure while preserving production authority; benchmark cannot become runtime truth |

## Step 5.1 — Recovery State Classifier

Accepted dispositions distinguish terminal Runs, ACTIVE ownership, resumable terminal evidence, expired-generation candidates, unowned dispatch ambiguity, and RELEASED evidence gaps. Classification remains read-only.

## Step 5.2 — Durable Dispatch Attempt Ledger

Accepted dispatch history distinguishes durable publication intent/outcomes around broker delivery:

```text
REQUESTED
    ↓
broker send
    ↓
ENQUEUED / PUBLISH_FAILED
```

PostgreSQL does not claim atomicity with Redis.

## Step 5.3 — Idempotent Task Reconciler

Recovery publication is allowed only after fresh locked PostgreSQL revalidation. Takeover receives a fresh dispatch identity and monotonic generation; expired `dispatch_id` and `run_token` are never reused.

## Step 5.4 — DAG-wide Run Reconciliation

READY/BLOCKED/in-flight/completed state is reconstructed from persisted DAG dependencies, accepted worker evidence, integration history, lease state, and Step 5.3 publication authority.

## Step 5.5 — Durable Human Pause / Resume

Phase 6 completed durable Human Gate reconstruction, typed decision persistence, exact Git/policy/evidence revalidation, bounded repair, deterministic verification, and crash/GC-safe repair staging refs.

## Step 5.6 — Causal Trace Correlation

Run → Task → Dispatch → Generation → Agent/Tool/Verifier spans are diagnostic-only. Raw prompts/completions, Tool bodies, repository content, credentials, and `run_token` stay outside trace payloads.

## Step 5.7 — Operator Recovery / Approval Surface

The only new operator mutation request is `ADVANCE_RUN`. The browser receives and submits only an opaque server-issued action identity. Fresh server-side plan reconstruction, durable dispatch history, Step 5.4, and Step 5.3 decide whether work may advance.

---

# Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance

Step 5.8 adds no recovery mechanism. It converts the accepted V1.1 semantics into a deterministic failure matrix.

Versioned manifest:

`benchmarks/v1_1/chaos-recovery.json`

Current candidate suite:

- schema version: `1`;
- suite version: `0.2.0`;
- required fault domains: **10**;
- frozen invariants: **7**.

Required deterministic scenarios:

1. C01 — worker/process disappears while lease is ACTIVE;
2. C02 — expired generation attempts a late evidence write;
3. C03 — expired generation attempts a late Git mutation;
4. C04 — terminal worker evidence exists but downstream controller/completion crashes;
5. C05 — duplicate reconcilers race to recover the same task;
6. C06 — broker publish fails after durable dispatch intent;
7. C07 — process restarts while Human Gate is pending;
8. C08 — multi-task DAG resumes without rerunning completed dependencies;
9. C09 — concurrent Operator `ADVANCE_RUN` requests race on one recovery opportunity;
10. C10 — repair evidence persists, then process crashes before integration Git CAS.

Frozen invariants:

- `AT_MOST_ONE_MUTATION`;
- `STALE_GENERATION_FENCED`;
- `UNKNOWN_STATE_NOT_GUESSED`;
- `FAILURE_NOT_SUCCESS`;
- `COMPLETED_WORK_NOT_RERUN`;
- `HUMAN_DECISION_DURABLE`;
- `OBSERVABILITY_NOT_AUTHORITY`.

Implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Strict implementation evidence:

- Backend Quality #956 (`32559638164`): **PASS**;
- Alembic round trip: **PASS**;
- verifier image + Ruff: **PASS**;
- V1 deterministic demos: **5 / 5 PASS**;
- V1.1 chaos recovery matrix: **10 / 10 PASS**;
- full pytest: **442 passed, 1 warning in 42.41s**;
- Frontend Quality #255 (`32559638135`): **PASS**.

Chaos suite SHA-256:

`088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`

## Remaining acceptance protocol

The implementation is complete, but V1.1 is intentionally not accepted yet. The remaining state transition is procedural and evidence-bound:

```text
implementation/hardening double-green        ✅
        ↓
complete candidate ledger                    ← CURRENT
        ↓
Backend + Frontend candidate-ledger CI        ⏳
        ↓
write Step 5.8 / Phase 5 / V1.1 ACCEPTED
        ↓
accepted-state Backend + Frontend CI          ⏳
        ↓
restore PR #41 stacked base
```

Only after both remaining CI layers pass may this roadmap say:

```text
Step 5.8 — ACCEPTED / COMPLETE
Phase 5 — ACCEPTED / COMPLETE
V1.1 — ACCEPTED / COMPLETE
```

---

# Out of scope for V1.1 unless deliberately amended

- arbitrary self-modifying agent policies;
- autonomous force-push/rebase repair;
- multi-region consensus;
- Kubernetes operator/controller implementation;
- distributed transaction claims between PostgreSQL and Redis without an explicit outbox design;
- using trace data, queue depth, benchmark output, or model confidence as task-success authority;
- silently retrying semantic/test failures under the label of crash recovery.

## Engineering / interview value

V1.1 makes concrete the production questions that distinguish a toy Agent loop from a durable runtime:

- How do you distinguish worker loss from replay authority?
- How do dispatch attempt, lease, generation and `run_token` differ?
- How do you prevent duplicate recovery controllers and Operator clicks from duplicating mutation?
- How do stale generations lose evidence and Git write authority permanently?
- How do you resume from terminal or repair evidence instead of rerunning expensive Agent work?
- How do Human decisions survive process restarts without bypassing deterministic gates?
- How do you prove crash recovery under deliberate fault injection rather than only by architecture diagrams?

Step 5.8 is the final V1.1 acceptance proof for those boundaries.
