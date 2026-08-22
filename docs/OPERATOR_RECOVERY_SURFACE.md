# Step 5.7 — Operator Recovery / Approval Surface

Status: **ACCEPTANCE CANDIDATE / NOT YET ACCEPTED**

## Goal

Step 5.7 turns the durable recovery facts from Steps 5.1–5.6 into a bounded operator-facing control surface without creating a second scheduler or allowing a browser/trace snapshot to authorize runtime mutation.

Frozen boundary:

> **Operator intent may request recovery work; only fresh server-side PostgreSQL, DAG, Git, lease, dispatch-ledger, and fencing facts may authorize that work.**

The anti-pattern is explicitly forbidden:

```text
Causal Trace says retry is safe
        ↓
direct retry / replay
```

The accepted direction is:

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

The first and only new Step 5.7 mutating command is intentionally narrow:

```text
ADVANCE_RUN
```

`ADVANCE_RUN` does **not** mean "force retry". It means: ask the existing durable controller/reconciler to re-enter the Run from current durable facts.

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

Every mutating action receives an opaque server-owned SHA-256 action id computed from the durable semantic state that justified the action.

The fingerprint excludes wall-clock observation time so a harmless refresh does not invalidate the action, but includes authority-bearing state such as:

- Run status;
- DAG digest and topology;
- per-task frontier state;
- lease state and generation;
- current lease dispatch identity where present;
- accepted worker terminal evidence identity/status;
- evidence-bound execution base where present;
- durable dispatch attempts and their publication outcomes.

Binding dispatch-attempt history closes a TOCTOU gap between publication and lease acquisition. Once a newer `REQUESTED`, `ENQUEUED`, or `PUBLISH_FAILED` attempt exists, the old action fingerprint changes even if the task still appears under the previous expired lease generation.

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

The actual at-most-one publication guarantee still comes from Step 5.3's fresh locked reconciliation path, not from the UI suppression.

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

The operator audit record proves only that an operator requested a server-issued action. It is not proof that a redispatch, merge, repair, or finalization occurred. Those outcomes continue to be proven by existing typed runtime evidence.

## Persistence boundary

Step 5.7 adds no migration.

`OPERATOR_ACTION` uses the existing generic typed/hash-validated evidence table. Runtime-event projection for this new evidence kind is isolated in `OperatorAwarePostgresEvidenceStore`, a narrow subclass of the accepted `PostgresEvidenceStore`.

This preserves the Step 5.6 persistence implementation unchanged, including:

- canonical hashing;
- typed decode validation;
- append-key idempotency;
- terminal Run append close;
- task `run_token` fencing where task-scoped;
- evidence/event transaction behavior.

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

The required invariant is:

> **Concurrent operator requests may race to request recovery, but they must not create more than one new broker publication for the same durable recovery opportunity.**

The accepted publication authority remains Step 5.3. Step 5.7 can only cause that authority to be re-entered.

## Acceptance target

The implementation/hardening head `434f5d2704afe3428366dd5e0406b8e061d52640` has passed strict Backend and Frontend CI, including **426 backend tests + 5/5 deterministic demos**.

Step 5.7 remains **NOT YET ACCEPTED** until all of the following are true:

1. read-only operator plan is deterministic and bounded;
2. action ids are server-owned and stale-safe;
3. action ids bind durable dispatch-attempt history;
4. action execution performs fresh plan rebuild immediately before mutation;
5. `ADVANCE_RUN` delegates to existing accepted runtime authorities;
6. ACTIVE/RELEASED/terminal states fail closed;
7. a newer dispatch suppresses stale duplicate action advertising;
8. duplicate/concurrent action requests produce at most one new publication;
9. operator request audit is typed and durable;
10. browser cannot submit dispatch/generation/token/Git authority;
11. frontend renders server-advertised actions only;
12. complete candidate ledger passes Backend + Frontend strict CI;
13. the final accepted-state ledger head passes Backend + Frontend strict CI again.

The detailed candidate evidence is recorded in `docs/STEP_5_7_ACCEPTANCE.md`.
