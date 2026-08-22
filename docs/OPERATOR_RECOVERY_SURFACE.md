# Step 5.7 — Operator Recovery / Approval Surface

Status: **ACCEPTED / COMPLETE**

## Goal

Step 5.7 turns durable recovery facts from Steps 5.1–5.6 into a bounded operator-facing control surface without creating a second scheduler or allowing a browser/trace snapshot to authorize runtime mutation.

Frozen boundary:

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

Forbidden:

```text
Causal Trace says retry is safe
        ↓
direct retry / replay
```

Accepted:

```text
Recovery / Causal Trace read models
        ↓ diagnostic only
Operator selects a server-advertised action
        ↓ request only
Fresh server-side plan rebuild
        ↓
action identity must still match
        ↓
existing accepted runtime authority
        ↓
Step 5.4 / Step 5.3 / Durable Controller
```

## Mutating scope

The only new Step 5.7 mutating command is intentionally narrow:

```text
ADVANCE_RUN
```

`ADVANCE_RUN` does **not** mean "force retry". It asks the existing durable controller/reconciler to re-enter the Run from current durable facts.

For a multi-task Run, execution delegates to `DurableMultiAgentRunController.advance(run_id)`. For a single-task Run, execution delegates to the accepted `DAGRunReconciler`, which in turn delegates any legal publication to Step 5.3 `IdempotentTaskReconciler`.

No new broker publisher, lease acquirer, scheduler truth, Git merge authority, verifier bypass, or terminal-state override is introduced by Step 5.7.

## Read model

The operator plan is built from:

- persisted Run/Task truth;
- persisted validated `TaskDAG`;
- one database-time lease snapshot;
- Step 5.1 `RecoveryStateClassifier`;
- Step 5.4 `DAGRunReconciliationPlanner`;
- dependency-aware execution-base resolution;
- durable dispatch-attempt history;
- optional Step 5.6 Causal Trace as a separate diagnostic view.

The browser receives the resulting task states and a bounded list of server-advertised operator actions.

Causal Trace remains explanatory only. The runtime never consumes a trace projection as permission to mutate.

## Action identity and staleness

Every mutating action receives an opaque server-owned SHA-256 `action_id` computed from the durable semantic state that justified the action.

The fingerprint excludes wall-clock observation time so a harmless refresh does not invalidate the action, but includes:

- Run status;
- DAG digest and topology;
- per-task frontier state;
- lease state and generation;
- current lease dispatch identity where present;
- accepted worker terminal evidence identity/status;
- evidence-bound execution base where present;
- durable dispatch attempts and their publication outcomes.

Binding dispatch-attempt history closes the TOCTOU gap between publication and lease acquisition. Once a newer `REQUESTED`, `ENQUEUED`, or `PUBLISH_FAILED` attempt exists, the old action fingerprint changes even if the task still appears under the previous expired lease generation.

The browser submits only the action id. It does not submit task ids, dispatch ids, lease generation, `run_token`, Git SHAs, branch names, parent commits, or execution bases as mutation authority.

Immediately before mutation, the server rebuilds the operator plan from fresh durable facts. If the action id is no longer advertised, the request fails as stale before audit or runtime mutation.

## Dispatch-aware action advertising

A task being a reconciliation candidate is not by itself sufficient to render an Operator button.

For an expired generation:

```text
current expired lease dispatch == latest durable dispatch attempt
        ↓
ADVANCE_RUN may be useful

newer durable dispatch attempt already exists
        ↓
no duplicate ADVANCE_RUN is advertised
```

This avoids a misleading UI window after broker publication but before the replacement worker acquires the next lease generation.

The actual at-most-one publication guarantee remains Step 5.3's fresh locked reconciliation path, not UI suppression.

## Mutation path

```text
POST opaque operator action id
        ↓
reject query/body authority selectors
        ↓
rebuild fresh operator plan
        ↓
exact action id still advertised?
   no ─────────────→ 409 stale / no mutation
   yes
        ↓
persist typed OPERATOR_ACTION request audit
        ↓
existing Durable Controller / DAG Run Reconciler
        ↓
Step 5.3 locked revalidation before broker publication
```

The operator audit proves only that an operator requested a server-issued action. It is not proof that a redispatch, merge, repair, or finalization occurred. Those outcomes continue to be proven by existing typed runtime evidence.

## Persistence boundary

Step 5.7 adds no migration.

`OPERATOR_ACTION` uses the existing generic typed/hash-validated evidence table. Runtime-event projection for this new evidence kind is isolated in `OperatorAwarePostgresEvidenceStore`, a narrow subclass of the accepted `PostgresEvidenceStore`.

This preserves the Step 5.6 persistence implementation unchanged, including canonical hashing, typed decode validation, append-key idempotency, terminal Run append close, task `run_token` fencing where task-scoped, and evidence/event transaction behavior.

## Fail-closed states

Step 5.7 preserves existing refusal semantics:

- terminal Run → cannot reopen and advertises no action;
- ACTIVE lease owner → cannot be raced and advertises no action;
- RELEASED lease without terminal evidence → cannot be silently reacquired;
- stale action fingerprint → rejected before mutation;
- newer dispatch already published → stale advance is not re-advertised;
- corrupt Run/DAG/lease/evidence identity → rejected;
- unsupported operator action → rejected;
- browser-authored authority selectors → rejected.

## Product API boundary

Read endpoint:

```text
GET /api/v1/runs/{run_id}/operator-recovery
```

Mutation-request endpoint:

```text
POST /api/v1/runs/{run_id}/operator-actions/{action_id}
```

The POST accepts no body and no query parameters. Server authority values therefore cannot be smuggled through the browser request.

## Frontend boundary

The Run Dashboard shows `OperatorRecoveryPanel` as a projection of server-owned state.

It may show:

- current recovery/frontier state;
- per-task lease/frontier summary;
- diagnostic wording explaining that Causal Trace is non-authoritative;
- server-advertised action buttons;
- explicit no-action state.

The browser must not synthesize an action from a local interpretation of trace data. Buttons are rendered only from `actions[]` returned by the server, and command submission sends only `(runId, actionId)`.

The existing Human Gate UI remains the narrower merge-conflict approval mechanism from Step 5.5 / Phase 6. Step 5.7 does not weaken its exact conflict-evidence and Git binding.

## Concurrency boundary

Real PostgreSQL coverage models an actual expired generation and submits two concurrent requests for the same action id.

Accepted invariant:

> **Concurrent operator requests may race to request recovery, but they must not create more than one new broker publication for the same durable recovery opportunity.**

The publication authority remains Step 5.3. Step 5.7 can only cause that authority to be re-entered.

## Acceptance evidence

Implementation/hardening head:

`434f5d2704afe3428366dd5e0406b8e061d52640`

- Backend Quality #942 (`32557865629`): **PASS**, including **426 tests + 5/5 demos**.
- Frontend Quality #235 (`32557865642`): **PASS**.

Complete candidate ledger head:

`030b50b2c122155fed7b53ecbe352550b9c79dd9`

- Backend Quality #947 (`32558218574`): **PASS**, including **426 passed, 1 warning in 39.38s + 5/5 demos**.
- Frontend Quality #240 (`32558218578`): **PASS**.
- PR #40 had **0 inline review threads** at the acceptance transition.

Detailed evidence is recorded in `docs/STEP_5_7_ACCEPTANCE.md`.

Step 5.7 is therefore **ACCEPTED / COMPLETE**. The accepted-state head containing this final status transition must independently pass Backend and Frontend Quality one final time; that external result completes the final acceptance condition without requiring a self-referential follow-up edit.

## Next boundary

Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance — remains unaccepted. It must systematically validate crash/retry/idempotency behavior across PostgreSQL, broker publication, lease generations, `run_token` fencing, worker evidence, Git objects, integration, repair, and process restarts.
