# Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance

Status: **IN DEVELOPMENT / NOT ACCEPTED**

## Goal

Step 5.8 does not add a new recovery authority. It deliberately breaks the already accepted V1.1 runtime at controlled crash/race boundaries and proves that recovery still obeys durable evidence, fencing, Git parentage, dispatch-ledger, Human Gate, and Operator-action authority.

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
real production authority components
(PostgreSQL / dispatch ledger / lease / run_token / Git / controller / Human Gate)
        ↓
scenario invariant assertions
        ↓
structured benchmark result
```

The benchmark result is evidence about the runtime test suite. It is not runtime success authority for any user Run.

## Required fault matrix

The initial Step 5.8 matrix must cover at least:

| ID | Fault boundary | Required proof |
| --- | --- | --- |
| C01 | worker/process disappears while lease is ACTIVE | no takeover before lease expiry; recovery is derived from durable lease time |
| C02 | expired generation performs a late evidence write | stale `run_token` / generation is rejected; accepted evidence is unchanged |
| C03 | expired generation performs a late Git mutation | stale generation cannot mutate authoritative Git state |
| C04 | terminal worker evidence persists, controller/completion crashes | restart resumes from accepted terminal evidence without rerunning semantic work |
| C05 | duplicate reconcilers race on the same expired task | at most one fresh dispatch/publication wins |
| C06 | broker publish fails after durable dispatch intent | PostgreSQL records ambiguity/failure; runtime does not claim execution or success |
| C07 | process restarts while Human Gate is pending | pending decision remains reconstructible and resume still revalidates exact evidence/Git state |
| C08 | multi-task DAG restarts after completed dependencies | completed dependencies are not rerun; only legal frontier work may advance |

The acceptance matrix will be extended where existing Phase 6/Step 5.7 boundaries expose additional high-risk crash windows, especially integration repair staging refs and Operator `ADVANCE_RUN` publication races.

## Cross-scenario invariants

Every scenario declares which of these invariants it proves:

- `AT_MOST_ONE_MUTATION` — one recovery opportunity cannot produce duplicate legal publication/integration mutation;
- `STALE_GENERATION_FENCED` — expired/stale actors never regain evidence or Git write authority;
- `UNKNOWN_STATE_NOT_GUESSED` — broker/process absence or an evidence gap remains explicit ambiguity/blocking rather than inferred success/retry;
- `FAILURE_NOT_SUCCESS` — infrastructure failure cannot be converted into task/Run success;
- `COMPLETED_WORK_NOT_RERUN` — accepted terminal work is resumed from evidence instead of recomputed by default;
- `HUMAN_DECISION_DURABLE` — pending/decided Human Gate state survives service/process reconstruction and remains evidence-bound;
- `OBSERVABILITY_NOT_AUTHORITY` — Trace/benchmark/operator projections cannot themselves authorize runtime mutation.

## Harness boundary

Step 5.8 will add a versioned manifest and a benchmark runner. The runner may select and execute deterministic pytest scenario node IDs, enforce manifest bounds, timeouts, duplicate checks, and produce a structured summary.

It must not:

- monkey-patch production authority checks to make scenarios pass;
- treat Redis queue inspection as truth;
- use sleep-only assertions where PostgreSQL/Git state can be checked directly;
- accept browser or manifest supplied `run_token`, generation, Git SHA, or dispatch identity as runtime authority;
- silently skip a required scenario;
- aggregate a partial pass into V1.1 acceptance.

## V1.1 acceptance gate

V1.1 cannot be marked `ACCEPTED / COMPLETE` until:

1. the versioned Step 5.8 chaos manifest validates deterministically;
2. every required fault domain is represented by an executable scenario;
3. all scenarios pass against production authority components;
4. Backend strict CI runs the chaos benchmark as an explicit gate;
5. pre-existing V1 demos and full pytest remain green;
6. the implementation head passes strict CI;
7. an acceptance/design/progress candidate ledger passes strict CI;
8. the final accepted-state ledger head passes strict CI again.

Until then, Step 5.8 and V1.1 remain **IN DEVELOPMENT / NOT ACCEPTED**.
