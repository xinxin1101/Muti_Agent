# Step 5.7 — Operator Recovery / Approval Surface

Status: **IN DEVELOPMENT / NOT ACCEPTED**

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

## Initial scope

The first mutating action is intentionally narrow:

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
- optional Step 5.6 Causal Trace as a separate diagnostic view.

The browser receives the resulting task states and a bounded list of server-advertised operator actions.

## Action identity and staleness

Every mutating action receives an opaque server-owned SHA-256 action id computed from the durable semantic state that justified the action. The fingerprint excludes wall-clock observation time so a harmless refresh does not invalidate the action, but includes authority-bearing state such as:

- Run status;
- DAG digest;
- per-task frontier state;
- lease state and generation;
- current dispatch identity where present;
- accepted worker terminal evidence identity/status;
- evidence-bound execution base where present.

The browser submits only the action id. It does not submit task ids, dispatch ids, lease generation, `run_token`, Git SHAs, branch names, or parent commits as mutation authority.

Immediately before mutation, the server rebuilds the operator plan from fresh durable facts. If the action id is no longer advertised, the request fails with a stale/conflict response.

## Mutation path

```text
POST operator action id
        ↓
rebuild fresh operator plan
        ↓
exact action id still advertised?
   no ─────────────→ 409 stale / no mutation
   yes
        ↓
persist typed operator request audit
        ↓
existing Durable Controller / DAG Run Reconciler
        ↓
Step 5.3 locked revalidation before broker publication
```

The operator audit record proves that a human requested an action; it is not proof that a redispatch, merge, repair, or finalization occurred. Those outcomes continue to be proven by the existing typed runtime evidence.

## Fail-closed states

Step 5.7 must preserve existing refusal semantics:

- terminal Run → cannot reopen;
- ACTIVE lease owner → cannot be raced;
- RELEASED lease without terminal evidence → cannot be silently reacquired;
- stale action fingerprint → rejected;
- corrupt Run/DAG/lease/evidence identity → rejected;
- unsupported operator action → rejected;
- browser-authored authority selectors → rejected.

## Frontend boundary

The Run Dashboard may show:

- current recovery summary;
- per-task recovery/frontier state;
- causal-trace link/diagnostic context;
- server-advertised action buttons;
- explicit warnings for blocked states.

The browser must not synthesize an action from a local interpretation of trace data. Buttons are rendered only from actions returned by the server.

## Acceptance target

Step 5.7 is not accepted until all of the following are proven:

1. read-only operator plan is deterministic and bounded;
2. action ids are server-owned and stale-safe;
3. action execution performs fresh plan rebuild immediately before mutation;
4. `ADVANCE_RUN` delegates to existing accepted runtime authorities;
5. ACTIVE/RELEASED/terminal states fail closed;
6. duplicate/concurrent action requests do not create duplicate runtime mutation;
7. operator request audit is typed and durable;
8. browser cannot submit dispatch/generation/token/Git authority;
9. frontend renders server-advertised actions only;
10. Backend and Frontend strict CI are green;
11. final acceptance/progress ledger head independently passes both workflows.

Until those conditions pass, Step 5.7 remains **IN DEVELOPMENT / NOT ACCEPTED**.
