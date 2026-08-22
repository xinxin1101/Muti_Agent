# Step 5.7 Acceptance — Operator Recovery / Approval Surface

Status: **ACCEPTED / COMPLETE**

## Frozen boundary

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

Step 5.7 is accepted because DevFlow now exposes a bounded operator-facing recovery surface without promoting Causal Trace, browser state, or an old recovery diagnosis into mutation authority.

## Accepted architecture

```text
Durable Run / Task / DAG facts
        +
RecoveryStateClassifier
        +
DAGRunReconciliationPlanner
        +
Lease snapshot
        +
Dispatch-attempt ledger
        +
Evidence-bound execution base
        ↓
OperatorRecoveryPlanner
        ↓
read-only OperatorRecoveryPlan
        ↓
server-advertised opaque action_id
        ↓
Operator chooses ADVANCE_RUN
        ↓
fresh plan rebuild
        ↓
exact action_id still advertised?
  no ───────────────→ reject stale / no mutation
  yes
        ↓
typed OPERATOR_ACTION request evidence
        ↓
Single task → DAGRunReconciler
Multi task  → DurableMultiAgentRunController
        ↓
Step 5.4 / Step 5.3 existing authority
        ↓
fresh locked publication / no-op / reject
```

Causal Trace remains a separate diagnostic read model. The operator surface may explain why an action is being considered, but Trace never authorizes the action.

## Accepted action scope

The only new mutating operator command in Step 5.7 is:

```text
ADVANCE_RUN
```

It means:

> Re-enter the already accepted durable controller/reconciler from current authoritative facts.

It does **not** mean force retry, replay a worker generation, acquire a lease from the browser, bypass Step 5.3 publication checks, bypass Git/evidence gates, or reopen a terminal Run.

No second scheduler, broker publisher, lease authority, Git authority, verifier, or success authority is introduced.

## Opaque action identity

`action_id` is a server-owned SHA-256 fingerprint over the semantic state that justified the advertised action.

The fingerprint binds:

- Run status;
- persisted DAG digest and topology;
- per-task frontier state;
- lease state and generation;
- current lease dispatch identity where present;
- accepted terminal worker evidence identity/status;
- evidence-bound execution base where present;
- durable dispatch-attempt history, including attempt state and broker acknowledgement/failure facts.

Wall-clock observation time is intentionally excluded, so a harmless refresh does not make the action stale.

The dispatch ledger is intentionally included, so a newly created `REQUESTED`, `ENQUEUED`, or `PUBLISH_FAILED` attempt changes the action identity even before a worker acquires the next lease generation.

## Fresh revalidation

The browser never sends an authority payload. It sends only the route Run identity and the opaque action id issued by the server.

Immediately before mutation, the server reconstructs the entire Operator Recovery Plan from fresh authoritative facts. If the supplied `action_id` is no longer present in `actions[]`, execution fails as stale before audit or runtime mutation.

Accepted ordering:

```text
old diagnostic/action snapshot
        ↓
operator request
        ↓
fresh authoritative reconstruction
        ↓
allow or reject
```

Forbidden ordering:

```text
old snapshot said retry was okay
        ↓
direct retry
```

## Dispatch-aware action advertising

Step 5.7 does not advertise `ADVANCE_RUN` merely because a task remains `RECONCILE_CANDIDATE`.

```text
EXPIRED generation
+ no newer durable publication attempt
        ↓
ADVANCE_RUN may be advertised

EXPIRED generation
+ newer REQUESTED / ENQUEUED / PUBLISH_FAILED attempt exists
        ↓
ADVANCE_RUN is not advertised from that stale frontier
```

This closes the misleading UI window after broker publication but before the replacement worker acquires the next lease generation. The actual at-most-one publication guarantee remains Step 5.3's fresh locked authority.

## Fail-closed states

Accepted behavior preserves existing durable recovery refusal semantics:

- terminal Run → no operator mutation advertised;
- ACTIVE owner → `WAIT_ACTIVE_OWNER`, no action advertised;
- RELEASED lease without terminal worker evidence → blocked recovery gap, no silent reacquisition;
- unsupported frontier/action → no mutation advertised;
- stale action fingerprint → HTTP 409 / no mutation;
- contradictory persisted facts → fail closed;
- broker failure → existing bounded broker error path;
- no browser-supplied token, generation, SHA, branch or dispatch identity can expand authority.

## Concurrent operator requests

Real PostgreSQL concurrency coverage starts from an actual expired worker generation, obtains one server-issued action id, then submits two concurrent operator requests.

Accepted invariant:

> **At most one fresh broker publication may result from concurrent requests for the same durable recovery opportunity.**

The test proves:

- the original expired generation has durable ENQUEUED publication history;
- the first legal recovery publication creates attempt 2;
- concurrent duplicate requests either converge on the same idempotent request evidence or become stale;
- broker send count is exactly one for the recovery publication;
- the durable attempt ledger contains attempt numbers `[1, 2]`, both acknowledged ENQUEUED;
- after the new dispatch exists but before lease acquisition, the refreshed operator plan advertises no duplicate advance;
- replaying the old action id is rejected as stale.

Publication authority remains Step 5.3; Step 5.7 only requests it.

## Typed audit evidence

A legal operator request is recorded as typed `OPERATOR_ACTION` evidence before runtime delegation.

The audit proves only:

```text
an operator requested this server-issued action
```

It does not prove worker redispatch, integration advance, repair success, or Run success. Those outcomes remain proven by the pre-existing dispatch, worker, verification, integration and terminal evidence chains.

Operator audit event-source projection is isolated in `OperatorAwarePostgresEvidenceStore`, a narrow subclass of the accepted `PostgresEvidenceStore`. The core persistence implementation remains unchanged from Step 5.6 accepted behavior.

No Step 5.7 database migration is required; `OPERATOR_ACTION` uses the existing typed/hash-validated generic evidence table.

## Product API

Read endpoint:

```text
GET /api/v1/runs/{run_id}/operator-recovery
```

Command endpoint:

```text
POST /api/v1/runs/{run_id}/operator-actions/{action_id}
```

The command endpoint rejects both query parameters and request bodies. The browser therefore cannot submit task id, dispatch id, lease generation, `run_token`, Git SHA, branch, integration parent, or execution base as mutation authority.

Only the server-issued opaque action id crosses the command boundary.

## Frontend

`OperatorRecoveryPanel` is mounted on the Run Dashboard.

It:

- renders the server-projected recovery/frontier state;
- renders only the server-provided `actions[]`;
- sends only `(runId, actionId)` using a no-body POST;
- does not infer a retry button from Causal Trace, lease text, task state, or local heuristics;
- displays a no-action state when the server advertises no legal mutation.

The pre-existing Human Gate panel remains the narrower merge-conflict approval surface from Step 5.5 / Phase 6. Step 5.7 does not weaken or replace its exact conflict-evidence binding.

## Step 5.7-specific tests

Coverage includes:

- `backend/tests/test_operator_recovery.py`
  - stable action identity across harmless observation-time changes;
  - dispatch-ledger binding;
  - ACTIVE/terminal no-action behavior;
  - stale action rejected before audit/mutation;
  - audit-before-delegation ordering;
  - single-task DAG reconciler delegation;
  - multi-task durable-controller delegation;
- `backend/tests/test_operator_api.py`
  - no-body command contract;
  - rejection of browser authority body/query selectors;
  - stale action conflict behavior;
- `backend/tests/test_operator_recovery_postgres.py`
  - ACTIVE owner read-only behavior on real PostgreSQL;
  - RELEASED evidence-gap blocking;
  - actual expired generation recovery;
  - concurrent operator requests publish at most once;
  - durable typed operator audit;
  - newer dispatch suppresses duplicate action advertising;
- `frontend/src/components/OperatorRecoveryPanel.test.tsx`
  - server-advertised action rendering;
  - opaque action-id-only request;
  - no synthesized button when the server provides no action.

The complete regression suite continues to cover prior persistence, leasing, fencing, reconciliation, Phase 6 integration and frontend behavior.

## Implementation-head evidence

Implementation/hardening head:

`434f5d2704afe3428366dd5e0406b8e061d52640`

Backend Quality **#942** (`32557865629`):

- PostgreSQL + Redis services: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- no Step 5.7 schema migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **426 passed, 1 warning in 34.21s**.

Frontend Quality **#235** (`32557865642`): **PASS** for locked install, TypeScript typecheck, lint, tests and production build.

## Candidate-ledger evidence

Complete candidate ledger head:

`030b50b2c122155fed7b53ecbe352550b9c79dd9`

Backend Quality **#947** (`32558218574`) on that candidate ledger: **PASS**, including Alembic round trip, verifier image, Ruff, fixture validation, **5/5 demos**, and **426 passed, 1 warning in 39.38s**.

Frontend Quality **#240** (`32558218578`) on the same candidate ledger: **PASS** for locked install, typecheck, lint, tests and production build.

PR #40 had **0 inline review threads** at the acceptance transition.

The warning in both backend runs is the existing FastAPI/Starlette TestClient deprecation and is unrelated to Step 5.7.

## Acceptance conclusion

Step 5.7 is **ACCEPTED / COMPLETE** because both the implementation/hardening head and the complete candidate ledger independently passed strict Backend and Frontend quality gates before this accepted status was written.

The accepted-state ledger head created by this final status transition must itself pass Backend Quality and Frontend Quality one final time. Once that external CI result is green, it completes the final acceptance condition without requiring a self-referential follow-up edit merely to copy that head's own CI identifiers back into this file.

PR #40 remains Draft and unmerged throughout the acceptance sequence.

## Explicitly deferred

Step 5.7 does not complete:

- a general arbitrary operator command language;
- browser-authored retry parameters;
- direct lease takeover controls;
- direct Git mutation controls;
- bypassable repair/integration approval;
- Step 5.8 Chaos / Recovery Benchmark and V1.1 final acceptance.

## Next authority transition

The next core question is:

```text
Can the durable runtime survive a systematic chaos matrix
across publish, lease, evidence, Git, integration and process boundaries
without fabricating success or duplicate mutation?
```

That is Step 5.8.
