# Step 5.7 Acceptance — Operator Recovery / Approval Surface

Status: **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**

## Frozen boundary

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

Step 5.7 is an acceptance candidate because DevFlow now exposes a bounded operator-facing recovery surface without promoting Causal Trace, browser state, or an old recovery diagnosis into mutation authority.

The implementation head has passed strict Backend and Frontend quality gates. This status remains **NOT YET ACCEPTED** until the complete candidate ledger head containing this file, `OPERATOR_RECOVERY_SURFACE.md`, `PROGRESS.md`, and both workflow path gates independently passes both quality workflows.

## Candidate architecture

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

## Action scope acceptance candidate

The only new mutating operator command in Step 5.7 is:

```text
ADVANCE_RUN
```

It means:

> Re-enter the already accepted durable controller/reconciler from current authoritative facts.

It does **not** mean:

- force retry;
- replay a worker generation;
- acquire a lease from the browser;
- bypass Step 5.3 publication checks;
- merge or repair without Git/evidence gates;
- reopen a terminal Run.

No second scheduler, broker publisher, lease authority, Git authority, verifier, or success authority is introduced.

## Opaque action identity

`action_id` is a server-owned SHA-256 fingerprint over semantic state that justified the advertised action.

The fingerprint binds:

- Run status;
- persisted DAG digest and topology;
- per-task frontier state;
- lease state;
- lease generation;
- current lease dispatch identity where present;
- accepted terminal worker evidence identity/status;
- evidence-bound execution base where present;
- durable dispatch-attempt history, including attempt state and broker acknowledgement/failure facts.

Wall-clock observation time is intentionally excluded, so a harmless refresh does not make the action stale.

The dispatch ledger is intentionally included, so a newly created `REQUESTED` or `ENQUEUED` attempt changes the action identity even before a worker acquires the next lease generation.

## Fresh revalidation candidate

The browser never sends an authority payload. It sends only:

```text
run_id from the route
opaque action_id from the server
```

Immediately before mutation, the server reconstructs the entire Operator Recovery Plan from fresh authoritative facts. If the supplied `action_id` is no longer present in `actions[]`, execution fails as stale before audit or runtime mutation.

This proves the desired ordering:

```text
old diagnostic/action snapshot
        ↓
operator request
        ↓
fresh authoritative reconstruction
        ↓
allow or reject
```

and forbids:

```text
old snapshot said retry was okay
        ↓
direct retry
```

## Dispatch-aware action advertising

Step 5.7 does not advertise `ADVANCE_RUN` merely because a task remains `RECONCILE_CANDIDATE`.

For an expired generation, an operator advance is useful only while the durable dispatch ledger has not already moved beyond the currently leased dispatch.

Therefore:

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

This prevents the UI from encouraging duplicate clicks while Step 5.3 is already carrying a newer durable publication attempt.

## Fail-closed recovery states

Candidate behavior preserves the existing durable recovery refusal semantics:

- terminal Run → no operator mutation advertised;
- ACTIVE owner → `WAIT_ACTIVE_OWNER`, no action advertised;
- RELEASED lease without terminal worker evidence → blocked recovery gap, no silent reacquisition;
- unsupported frontier/action → no mutation advertised;
- stale action fingerprint → HTTP 409 / no mutation;
- contradictory persisted facts → fail closed;
- broker failure → existing bounded broker error path;
- no browser-supplied token, generation, SHA, branch or dispatch identity can expand authority.

## Concurrent operator request candidate

Real PostgreSQL concurrency coverage starts from an actual expired worker generation, obtains one server-issued action id, then submits two concurrent operator requests.

Accepted candidate invariant:

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

The audit proves:

```text
an operator requested this server-issued action
```

It does **not** prove:

```text
a worker was redispatched
an integration advanced
a repair succeeded
a Run succeeded
```

Those outcomes remain proven by the pre-existing dispatch, worker, verification, integration and terminal evidence chains.

Operator audit event-source projection is isolated in `OperatorAwarePostgresEvidenceStore`, a narrow subclass of the accepted `PostgresEvidenceStore`. The core persistence implementation remains unchanged from the Step 5.6 accepted behavior.

No Step 5.7 database migration is required; `OPERATOR_ACTION` uses the existing typed/hash-validated generic evidence table.

## Product API candidate

Read endpoint:

```text
GET /api/v1/runs/{run_id}/operator-recovery
```

Command endpoint:

```text
POST /api/v1/runs/{run_id}/operator-actions/{action_id}
```

The command endpoint rejects both query parameters and request bodies. The browser therefore cannot submit:

- task id as mutation authority;
- dispatch id;
- lease generation;
- `run_token`;
- Git SHA;
- branch;
- integration parent;
- execution base.

Only the server-issued opaque action id crosses the command boundary.

## Frontend candidate

`OperatorRecoveryPanel` is mounted on the Run Dashboard.

It:

- renders the server-projected recovery/frontier state;
- renders only the server-provided `actions[]`;
- sends only `(runId, actionId)` using a no-body POST;
- does not infer a retry button from Causal Trace, lease text, task state, or local heuristics;
- displays a no-action state when the server advertises no legal mutation.

The pre-existing Human Gate panel remains the narrower merge-conflict approval surface from Step 5.5/Phase 6. Step 5.7 does not weaken or replace its exact conflict-evidence binding.

## Candidate tests

Step 5.7-specific coverage includes:

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

Exact implementation/hardening head before candidate ledger changes:

`434f5d2704afe3428366dd5e0406b8e061d52640`

Backend Quality run **#942** (`32557865629`) on that head:

- PostgreSQL + Redis services: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- no Step 5.7 schema migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **426 passed, 1 warning in 34.21s**.

Frontend Quality run **#235** (`32557865642`) on the same implementation head: **PASS** for locked install, TypeScript typecheck, lint, tests and production build.

The warning is the existing FastAPI/Starlette TestClient deprecation and is unrelated to Step 5.7.

## Candidate-ledger verification required

The candidate ledger head created by adding/updating:

- `docs/OPERATOR_RECOVERY_SURFACE.md`;
- this `docs/STEP_5_7_ACCEPTANCE.md`;
- `docs/PROGRESS.md`;
- `.github/workflows/backend-quality.yml`;
- `.github/workflows/frontend-quality.yml`;

must independently pass strict Backend Quality and Frontend Quality.

Only after that double-green result may this file transition to:

```text
Status: ACCEPTED / COMPLETE
```

That final status transition will itself create an accepted-state head which must pass both workflows one final time. PR #40 remains Draft and unmerged throughout the acceptance sequence.

## Explicitly deferred

Step 5.7 does not complete:

- a general arbitrary operator command language;
- browser-authored retry parameters;
- direct lease takeover controls;
- direct Git mutation controls;
- bypassable repair/integration approval;
- Step 5.8 Chaos / Recovery Benchmark and V1.1 final acceptance.

## Next authority transition after acceptance

When Step 5.7 is accepted, the next question becomes:

```text
Can the durable runtime survive a systematic chaos matrix
across publish, lease, evidence, Git, integration and process boundaries
without fabricating success or duplicate mutation?
```

That is Step 5.8. Until candidate-ledger and accepted-state CI are green, Step 5.7 remains **NOT YET ACCEPTED**.
