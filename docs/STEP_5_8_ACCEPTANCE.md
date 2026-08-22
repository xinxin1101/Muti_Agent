# Step 5.8 Acceptance — Chaos / Recovery Benchmark + V1.1 Acceptance

Status: **ACCEPTED / COMPLETE**

## Frozen boundary

> **Chaos may perturb delivery, liveness, timing, process continuity, and stale actors; it may not weaken the production authority model or replace production stores with an easier test-only truth.**

Step 5.8 is accepted because DevFlow now has a versioned deterministic chaos matrix that crosses the durable-runtime boundaries introduced in Steps 5.1–5.7 while exercising the accepted production authority paths rather than a test-only recovery implementation.

## Accepted proof surface

Manifest: `benchmarks/v1_1/chaos-recovery.json`

Suite:

- schema version `1`
- suite version `0.2.0`
- SHA-256 `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`
- ten required fault domains
- seven frozen invariants

The accepted scenarios are:

1. C01 — ACTIVE worker loss does not authorize takeover before durable lease expiry.
2. C02 — stale generation cannot append accepted evidence.
3. C03 — stale generation cannot mutate authoritative Git state.
4. C04 — terminal worker evidence survives controller/completion crash and semantic work is not rerun.
5. C05 — duplicate reconcilers race safely and produce at most one fresh recovery publication.
6. C06 — broker publication failure remains explicit and cannot be represented as execution or success.
7. C07 — pending Human Gate state survives process/service reconstruction and remains fingerprint-bound.
8. C08 — multi-task DAG recovery does not rerun completed dependencies and only advances the legal frontier.
9. C09 — concurrent Operator `ADVANCE_RUN` requests produce at most one publication and the old action becomes stale after durable state changes.
10. C10 — persisted bounded repair survives crash before integration Git CAS through a server-owned staging ref and is reused without rerunning the Agent.

Frozen invariants:

- `AT_MOST_ONE_MUTATION`
- `STALE_GENERATION_FENCED`
- `UNKNOWN_STATE_NOT_GUESSED`
- `FAILURE_NOT_SUCCESS`
- `COMPLETED_WORK_NOT_RERUN`
- `HUMAN_DECISION_DURABLE`
- `OBSERVABILITY_NOT_AUTHORITY`

## Authority result

The chaos harness proves existing authority; it does not become authority.

PostgreSQL remains the durable Run/Task/dispatch/evidence truth. Lease establishes liveness, generation/`run_token` establish mutation authority, Git refs/parentage/CAS establish integration authority, Human Gate decisions remain evidence-bound, Operator actions remain requests requiring fresh server-side revalidation, and Trace/benchmark/UI projections remain non-authoritative.

The accepted Step 5.8 result therefore answers the V1.1 question:

> **Under the frozen deterministic crash points, DevFlow preserves at-most-one legal mutation, permanent stale-generation fencing, explicit unknown state, no false success, no semantic rerun of accepted completed work, durable Human decisions, and no Operator/Trace/benchmark authority bypass.**

## CI evidence

Implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Backend Quality #956 (`32559638164`): **PASS** — migrations, verifier image, Ruff, V1 fixture validation, **5/5 control-plane demos**, **10/10 chaos scenarios**, **442 passed, 1 warning in 42.41s**.

Frontend Quality #255 (`32559638135`): **PASS**.

Complete candidate-ledger head:

`9c753404d48c577d7ec820d7e5bdea3ceb73df80`

Backend Quality #961 (`32559859061`): **PASS**.

Frontend Quality #260 (`32559859065`): **PASS**.

These two independent double-green layers satisfy the prerequisite for the accepted-state transition.

## Accepted version transition

The repository may now state:

```text
Step 5.8 — ACCEPTED / COMPLETE
Phase 5 — ACCEPTED / COMPLETE
V1.1 — ACCEPTED / COMPLETE
```

The accepted-state ledger head created by this transition must independently pass Backend Quality and Frontend Quality one final time. If that final external CI fails, the accepted state is invalid and must be corrected; a passing final run completes the acceptance protocol without requiring a self-referential documentation edit.

## PR state

PR #41 remains Draft and unmerged during the acceptance transition. After the accepted-state head passes both workflows, its temporary `main` base must be restored to `phase5/operator-recovery-surface`.
