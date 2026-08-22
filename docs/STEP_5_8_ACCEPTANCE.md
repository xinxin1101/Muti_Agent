# Step 5.8 Acceptance — Chaos / Recovery Benchmark + V1.1 Acceptance

Status: **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**

## Frozen boundary

> **Chaos may perturb delivery, liveness, timing, process continuity, and stale actors; it may not weaken the production authority model or replace production stores with an easier test-only truth.**

Step 5.8 is an acceptance candidate because DevFlow now has a versioned, deterministic chaos matrix that deliberately crosses the durable-runtime boundaries introduced in Steps 5.1–5.7 while continuing to exercise the accepted production authority paths.

The implementation/hardening head has passed strict Backend and Frontend quality gates. Step 5.8 and V1.1 remain **NOT YET ACCEPTED** until this complete acceptance/design/progress/workflow candidate ledger independently passes Backend and Frontend Quality, after which a final accepted-state ledger must pass both workflows again.

## What Step 5.8 proves

The benchmark is not a generic load test and is not an alternative recovery engine. It proves explicit invariants at dangerous crash/race boundaries:

```text
failure injection
        ↓
production durable authority
        ↓
PostgreSQL / lease / run_token / dispatch ledger / Git /
DAG reconciler / Human Gate / repair / Operator recovery
        ↓
fail closed or exactly one legal continuation
```

Frozen invariants:

1. `AT_MOST_ONE_MUTATION`
2. `STALE_GENERATION_FENCED`
3. `UNKNOWN_STATE_NOT_GUESSED`
4. `FAILURE_NOT_SUCCESS`
5. `COMPLETED_WORK_NOT_RERUN`
6. `HUMAN_DECISION_DURABLE`
7. `OBSERVABILITY_NOT_AUTHORITY`

## Versioned manifest

Manifest:

`benchmarks/v1_1/chaos-recovery.json`

Candidate version:

`0.2.0`

The typed `ChaosRecoveryManifest` requires all ten frozen fault domains and all seven invariants. Scenario IDs and pytest node IDs must be unique, pytest selectors are bounded to explicit backend test functions, and invalid/partial manifests fail validation before any scenario executes.

Required domains:

```text
LEASE
FENCING
GIT
PROCESS_RESTART
CONCURRENCY
BROKER
HUMAN_GATE
DAG
OPERATOR
REPAIR
```

## Ten deterministic scenarios

| ID | Fault | Production proof |
| --- | --- | --- |
| C01 | ACTIVE worker disappears | Step 5.3 returns `WAIT_ACTIVE_OWNER`; no takeover/publication before durable lease expiry |
| C02 | stale generation writes evidence | old `run_token` is rejected and stale evidence is absent |
| C03 | stale generation mutates Git | production queued-execution Git fence rejects stale generation; authoritative refs remain unchanged |
| C04 | terminal evidence exists but downstream process crashes | fresh reconciler returns `RESUME_TERMINAL_EVIDENCE`; semantic work is not republished |
| C05 | duplicate reconcilers race | real PostgreSQL locking/idempotency yields exactly one new publication |
| C06 | broker publish fails after durable intent | attempt becomes `PUBLISH_FAILED`; Run remains non-terminal and no worker-success evidence is invented |
| C07 | process restarts while Human Gate is pending | fresh service reconstructs the exact pending gate/fingerprint from PostgreSQL |
| C08 | DAG reconciler restarts after dependency completion | completed dependency stays completed and only legal downstream frontier may advance |
| C09 | concurrent Operator `ADVANCE_RUN` requests | one durable recovery opportunity produces at most one new broker publication and old action becomes stale |
| C10 | repair evidence persists then process crashes before Git CAS | staging ref preserves exact repair commit; fresh repair service reuses it without rerunning Agent and advances only through accepted Git CAS |

## Authority composition

### PostgreSQL / dispatch / lease / fencing

C01, C02, C04, C05, C06, C08 and C09 use the production PostgreSQL stores and accepted reconciler/dispatcher boundaries. Redis/broker observations never become truth; the durable dispatch ledger represents requested/enqueued/publish-failed facts without pretending to provide a distributed transaction.

### Git

C03 uses the production generation-aware Git fence. C10 uses the accepted repair staging-ref + Git CAS path, including reflog expiry and `git gc --prune=now`, proving that crash-safe object liveness does not make the staging ref runtime truth.

### Human Gate / repair

C07 and C10 separate two responsibilities:

- C07 proves pending Human authority survives service reconstruction;
- C10 proves already-authorized bounded repair output survives the database-to-Git continuation window and is reused rather than regenerated.

Human approval still cannot bypass verification, conflict binding, Git parentage, or integration CAS.

### Operator surface

C09 reuses the production Step 5.7 `OperatorRecoveryCoordinator` path. The browser/operator action remains an opaque request. Fresh server-side reconstruction and Step 5.3 publication authority decide whether mutation is still legal.

## Dedicated CI gate

Backend Quality now executes:

```text
Alembic round trip
        ↓
verification sandbox build
        ↓
Ruff
        ↓
V1 fixture validation
        ↓
5/5 deterministic control-plane demos
        ↓
10/10 deterministic V1.1 chaos scenarios
        ↓
full pytest regression
```

The chaos matrix is a separate blocking step, so a failed invariant cannot be hidden inside aggregate pytest output.

## Implementation/hardening evidence

Exact implementation/hardening head before candidate-ledger changes:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Backend Quality run **#956** (`32559638164`):

- PostgreSQL + Redis: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- no Step 5.8 migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- deterministic V1.1 chaos recovery benchmark: **10 / 10 PASS**;
- chaos suite version: `0.2.0`;
- chaos suite SHA-256: `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`;
- seven frozen invariants covered;
- pytest: **442 passed, 1 warning in 42.41s**.

Frontend Quality run **#255** (`32559638135`) on the same implementation head: **PASS** for locked install, typecheck, lint, tests and production build.

The warning is the existing FastAPI/Starlette TestClient deprecation and is unrelated to Step 5.8.

## No new runtime authority

Step 5.8 adds:

- typed benchmark schema;
- versioned scenario manifest;
- deterministic benchmark runner/CLI;
- fault-injection tests;
- a strict CI gate.

It does not add:

- a new scheduler;
- a new reconciler;
- a second lease/fencing implementation;
- a benchmark-only success path;
- browser-authored recovery authority;
- Trace-based mutation authority;
- automatic force repair/merge behavior.

The benchmark may prove existing authority; it cannot become that authority.

## Candidate-ledger verification required

The complete candidate ledger consists of:

- `docs/CHAOS_RECOVERY_BENCHMARK.md`;
- this `docs/STEP_5_8_ACCEPTANCE.md`;
- `docs/V1_1_ROADMAP.md`;
- `docs/PROGRESS.md`;
- Backend/Frontend workflow path gates.

This candidate head must independently pass strict Backend Quality and Frontend Quality while Step 5.8 and V1.1 still remain `NOT YET ACCEPTED`.

Only after that double-green result may the repository transition to:

```text
Step 5.8 — ACCEPTED / COMPLETE
Phase 5 — ACCEPTED / COMPLETE
V1.1 — ACCEPTED / COMPLETE
```

That status transition creates a new accepted-state head which must itself pass both workflows one final time. No acceptance claim may be derived from this file alone.

## PR state

PR #41 remains Draft and unmerged throughout the acceptance sequence. Its base is temporarily `main` only to obtain strict pull-request workflow execution; after final accepted-state CI succeeds, it must be restored to the stacked Step 5.7 base `phase5/operator-recovery-surface`.

Until candidate-ledger and accepted-state CI are independently green, Step 5.8 and V1.1 remain **NOT YET ACCEPTED**.
