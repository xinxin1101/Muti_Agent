# Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance

Status: **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**

## Goal

Step 5.8 does not add a new recovery authority. It deliberately breaks the already accepted V1.1 runtime at controlled crash/race boundaries and proves that recovery still obeys durable evidence, fencing, Git parentage, dispatch-ledger, Human Gate, repair, and Operator-action authority.

Frozen boundary:

> **Chaos may perturb delivery, liveness, timing, process continuity, and stale actors; it may not weaken the production authority model or replace production stores with an easier test-only truth.**

The acceptance question is:

> **After deterministic fault injection, can DevFlow still prove at-most-one legal mutation, permanent stale-generation fencing, no guessed truth, no false success, and no Operator/Trace authority bypass?**

## Benchmark shape

```text
versioned chaos manifest
        ↓
validated scenario registry
        ↓
deterministic fault injection
        ↓
production authority components
(PostgreSQL / dispatch ledger / lease / run_token / Git /
 reconciler / Human Gate / repair / Operator recovery)
        ↓
scenario invariant assertions
        ↓
structured benchmark result
```

The benchmark result is evidence about the runtime test suite. It is not runtime success authority for any user Run.

## Required fault matrix

The Step 5.8 manifest version `0.2.0` requires ten explicit fault domains:

| ID | Fault boundary | Required proof |
| --- | --- | --- |
| C01 | worker/process disappears while lease is ACTIVE | no takeover before lease expiry; recovery is derived from durable lease time |
| C02 | expired generation performs a late evidence write | stale `run_token` / generation is rejected; accepted evidence is unchanged |
| C03 | expired generation performs a late Git mutation | stale generation cannot mutate authoritative Git state |
| C04 | terminal worker evidence persists, controller/completion crashes | restart resumes from accepted terminal evidence without rerunning semantic work |
| C05 | duplicate reconcilers race on the same expired task | at most one fresh dispatch/publication wins |
| C06 | broker publish fails after durable dispatch intent | PostgreSQL records failure/ambiguity; runtime does not claim execution or success |
| C07 | process restarts while Human Gate is pending | pending gate remains reconstructible from PostgreSQL and exact evidence fingerprint |
| C08 | multi-task DAG restarts after completed dependencies | completed dependencies are not rerun; only legal frontier work may advance |
| C09 | concurrent Operator `ADVANCE_RUN` requests race on one expired generation | server action becomes stale after the first durable publication; at most one new broker publication occurs |
| C10 | bounded integration repair is persisted, then process crashes before integration Git CAS | staging ref keeps exact repair commit alive; fresh service reuses persisted repair without rerunning Agent and advances Git only through the accepted CAS path |

The manifest validator treats all ten domains as required. Removing an Operator or repair scenario therefore invalidates the suite instead of silently reducing coverage.

## Human Gate and repair split

Human approval and repair recovery are intentionally proven by separate scenarios:

```text
C07
pending Human Gate
        ↓ process/service restart
PostgreSQL reconstruction
        ↓
same unresolved gate + same fingerprint

C10
Human AUTHORIZE_REPAIR already bound
        ↓
bounded repair commit persisted
        ↓ crash before integration-ref CAS
server-owned staging ref preserves Git object
        ↓ fresh service / Git GC
reuse exact persisted repair commit
        ↓
accepted CAS + cleanup
```

C07 proves decision/gate durability. C10 proves that authorized repair output can survive the dangerous database-to-Git continuation window without rerunning the Agent or treating persistence alone as integration success.

## Operator race proof

C09 reuses the accepted Step 5.7 production path rather than introducing a chaos-only operator executor:

```text
expired generation
        ↓
server advertises one opaque ADVANCE_RUN action_id
        ↓
2 concurrent operator requests
        ↓
fresh revalidation + Step 5.3 publication authority
        ↓
exactly one new broker publication
        ↓
new dispatch attempt changes durable action identity
        ↓
old action becomes stale / no duplicate button
```

The Operator request remains a request only. Its audit record cannot prove redispatch, execution, repair, integration, or Run success.

## Cross-scenario invariants

Every scenario declares which of these invariants it proves:

- `AT_MOST_ONE_MUTATION` — one recovery opportunity cannot produce duplicate legal publication/integration mutation;
- `STALE_GENERATION_FENCED` — expired/stale actors never regain evidence or Git write authority;
- `UNKNOWN_STATE_NOT_GUESSED` — broker/process absence or an evidence gap remains explicit ambiguity/blocking rather than inferred success/retry;
- `FAILURE_NOT_SUCCESS` — infrastructure failure cannot be converted into task/Run success;
- `COMPLETED_WORK_NOT_RERUN` — accepted terminal/repair work is resumed from evidence instead of recomputed by default;
- `HUMAN_DECISION_DURABLE` — pending/decided Human Gate state survives service/process reconstruction and remains evidence-bound;
- `OBSERVABILITY_NOT_AUTHORITY` — Trace/benchmark/operator projections cannot themselves authorize runtime mutation.

## Harness boundary

Step 5.8 adds a versioned manifest and a benchmark runner. The runner selects exact deterministic pytest node IDs, enforces manifest bounds/timeouts/duplicate checks, and emits a typed structured summary.

It must not:

- monkey-patch production authority checks to make scenarios pass;
- treat Redis queue inspection as truth;
- replace PostgreSQL lease/dispatch/fencing semantics with an in-memory truth where real durable proof is required;
- use sleep-only assertions where PostgreSQL/Git state can be checked directly;
- accept browser or manifest supplied `run_token`, generation, Git SHA, or dispatch identity as runtime authority;
- let Human approval mean "merge anyway";
- let persisted repair evidence mean integration succeeded before Git CAS;
- silently skip a required scenario;
- aggregate a partial pass into V1.1 acceptance.

C10 intentionally reuses the already-accepted integration-repair crash/GC/CAS hardening test for the Git continuation window. C07 provides the complementary real-PostgreSQL Human Gate restart proof. The benchmark composes accepted production proofs; it does not create a second recovery engine.

## CI gate

Backend Quality runs the Step 5.8 matrix as a separate blocking command after the existing deterministic V1 control-plane demos and before full pytest:

```text
V1 fixtures
    ↓
5/5 control-plane demos
    ↓
Step 5.8 chaos manifest (10/10 required scenarios)
    ↓
full pytest regression
```

This prevents a partial chaos failure from being hidden inside the full regression count.

## Implementation candidate evidence

Exact implementation/hardening head:

`572995329fb0422bc2de72d83db6096cda70c8d6`

Backend Quality #956 (`32559638164`): **PASS** — Alembic round trip, verifier image, Ruff, V1 fixture validation, **5/5 demos**, **10/10 chaos scenarios**, and **442 passed, 1 warning in 42.41s**.

Chaos suite:

- version: `0.2.0`;
- SHA-256: `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`;
- scenario count: **10**;
- invariant count: **7**.

Frontend Quality #255 (`32559638135`): **PASS**.

No Step 5.8 database migration is required.

## V1.1 acceptance gate

V1.1 cannot be marked `ACCEPTED / COMPLETE` until:

1. the versioned Step 5.8 chaos manifest validates deterministically;
2. all ten required fault domains are represented by executable scenarios;
3. all seven frozen invariants have explicit scenario coverage;
4. all scenarios pass through the dedicated Backend CI chaos gate;
5. pre-existing V1 demos and full pytest remain green;
6. Frontend strict CI remains green because Step 5.8 may not regress accepted product surfaces;
7. the final implementation/hardening head passes strict Backend + Frontend CI;
8. an acceptance/design/progress/workflow candidate ledger passes strict Backend + Frontend CI;
9. only then may Step 5.8 and V1.1 transition to `ACCEPTED / COMPLETE`;
10. the final accepted-state ledger head must pass strict Backend + Frontend CI again.

The implementation conditions are now satisfied, but this document remains **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED** until the complete candidate ledger passes both workflows. The final status transition must then pass both workflows again.
