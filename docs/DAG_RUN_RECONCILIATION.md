# Step 5.4 — DAG-wide Run Reconciliation

## Purpose

Step 5.4 restores the legal execution frontier of a persisted multi-task Run after process loss or recovery inspection. It composes already-accepted durable facts; it does not persist a replacement scheduler state.

Frozen boundary:

> **Run reconciliation may reconstruct the scheduling frontier from validated DAG and accepted terminal task facts; it may not create a second scheduler truth.**

Step 5.4 therefore answers only:

- which Tasks are already terminal from accepted Worker evidence;
- which Tasks are blocked by accepted upstream failure;
- which Tasks still wait for dependencies;
- which DAG-ready Tasks still have a live owner;
- which DAG-ready Tasks are blocked by a recovery evidence gap;
- which DAG-ready Tasks have a trustworthy execution base;
- which Tasks may be nominated to the accepted Step 5.3 task reconciler.

It does not directly create dispatch attempts, acquire leases, advance generations, issue `run_token`s, mutate Git, mark Tasks successful, finalize Runs, or alter the DAG.

## Authority topology

```text
PersistedRunSnapshot
        +
hash-validated PersistedDAGSnapshot
        +
accepted WORKER_EXECUTION evidence
        +
fresh database-time lease facts
        +
accepted MERGE_QUEUE_SNAPSHOT history
        ↓
RecoveryStateClassifier
        +
TaskDAG ready/blocked semantics
        +
EvidenceBoundTaskExecutionBaseResolver
        ↓
DAGRunReconciliationPlan
        ↓
RECONCILE_CANDIDATE task ids only
        ↓
Step 5.3 IdempotentTaskReconciler
        ↓
fresh locked PostgreSQL revalidation
        ↓
durable dispatch publication path
```

The plan is a read projection and nomination. Step 5.3 remains the only recovery dispatch authority.

## Reconstructed frontier states

Step 5.4 derives, but does not independently persist, these states:

```text
RUN_TERMINAL
SUCCEEDED
FAILED
BLOCKED_UPSTREAM_FAILURE
WAIT_DEPENDENCIES
WAIT_ACTIVE_OWNER
BLOCKED_RECOVERY_GAP
WAIT_INTEGRATION_BASE
RECONCILE_CANDIDATE
```

They are recomputed from current durable facts on every reconciliation pass.

### Terminal facts

A Task is `SUCCEEDED` only when accepted typed `WORKER_EXECUTION` evidence says `SUCCEEDED`.

A Task is `FAILED` only when accepted typed `WORKER_EXECUTION` evidence says `FAILED`.

A completed dependency is never rerun merely because the original scheduler process disappeared.

### DAG semantics

The existing immutable `TaskDAG` remains the topology authority:

```text
TaskDAG.ready_task_ids(completed, failed)
TaskDAG.blocked_task_ids(failed)
```

Step 5.4 does not duplicate dependency algorithms in a second mutable scheduler.

If accepted failure makes a descendant blocked but that descendant already contains terminal execution or live ownership facts, reconciliation treats the contradiction as persistence/runtime corruption rather than inventing a new state.

## DAG readiness is not sufficient execution authority

A critical Step 5.4 hardening is the distinction:

```text
DAG READY
    !=
safe execution base proven
```

For a root Task, the frozen Run base is its execution base.

For a dependent Task, accepted dependency success alone does not prove that the dependency code is present in the Task's starting Git tree. The dependent Task must also have accepted integration history proving its direct dependencies are already contained in the current integration head.

Therefore:

```text
DAG READY
        +
accepted dependency integration
        +
trusted execution-base provenance
        ↓
eligible for Step 5.3 nomination
```

## Evidence-bound execution-base resolver

`EvidenceBoundTaskExecutionBaseResolver` selects the Git base without accepting SHA input from Redis, browser state, or a stale recovery plan.

### Root Task

```text
Task has no dependencies
        ↓
PersistedRunSnapshot.base_commit
        ↓
RUN_BASE
```

### Dependent Task

```text
validated persisted DAG
        +
successful WORKER_EXECUTION commit pairs
        +
cumulative MERGE_QUEUE_SNAPSHOT history
        ↓
all direct dependencies integrated?
        ↓ yes
latest accepted integration head
        ↓
MERGE_QUEUE_SNAPSHOT execution base
```

The selected base carries provenance:

- `source_evidence_id`;
- `source_evidence_sha256`;
- `integration_ref`;
- selected `commit_sha`.

## Merge-history validation

The resolver rejects merge history that cannot represent one deterministic cumulative integration sequence.

Checks include:

- Run base equals persisted Run base;
- merge attempt count is bounded;
- Task ids belong to the validated DAG;
- attempt order respects deterministic topological order;
- dependencies are already integrated before a dependent attempt;
- each merge attempt has matching successful Worker `(task_base_commit, task_commit)` evidence;
- root Task commits start from the frozen Run base;
- dependent Task commits start from a prior integration head containing their dependencies;
- snapshot histories may extend but may not regress or diverge;
- integration ref cannot silently change;
- a stopped conflict history cannot later advance without an accepted resolution path.

## Git remains code-state truth

When a managed workspace is available, PostgreSQL evidence chooses the candidate commit chain and Git reproduces it.

For every accepted Task commit:

```text
parents(task_commit) == (task_base_commit,)
```

For every accepted integration commit:

```text
parents(integration_commit) == (
    previous_integration_commit,
    task_commit,
)
```

A syntactically valid persisted SHA is not sufficient. Wrong parentage fails closed as persistence corruption.

This preserves the project split:

```text
PostgreSQL typed evidence = durable runtime truth
Git object graph           = code-state truth
```

## Worker execution fence

The queue envelope remains minimal:

```text
dispatch_id
run_id
task_id
```

It never carries:

```text
base_commit
integration_commit
branch
lease_generation
run_token
```

The queued worker reloads the persisted Run and resolves its execution base after receiving the message.

A multi-task queued worker without an evidence-bound execution-base resolver fails before backend/Git execution. Single-task legacy execution may continue to use the frozen Run base because no dependency integration exists.

## Step 5.3 delegation

Only Tasks derived as `RECONCILE_CANDIDATE` are passed to `IdempotentTaskReconciler`.

A candidate requires:

```text
Run == RUNNING
DAG-ready
no accepted terminal Worker evidence
lease == UNOWNED or EXPIRED
execution base proven
```

The Step 5.4 plan does not authorize the broker call. Step 5.3 re-locks Run/Task facts, rechecks dispatch history and ownership, creates durable REQUESTED intent, and holds publication authority across the broker observation.

This is intentional double validation:

```text
Step 5.4 = frontier nomination
Step 5.3 = fresh mutation authorization
```

## Concurrency model

Multiple `DAGRunReconciler` instances may reconstruct the same candidate frontier concurrently.

Step 5.4 does not add a Run-global scheduler lock merely to serialize read projections. Exactly-one fresh publication per Task continues to come from Step 5.3's PostgreSQL linearization and publication fencing.

For independent ready roots A and B:

```text
reconciler X → candidates A, B
reconciler Y → candidates A, B

both delegate to Step 5.3
        ↓
Task A: exactly one durable fresh dispatch
Task B: exactly one durable fresh dispatch
```

The reconstructed plan may become stale immediately after it is built; that is safe because it is not mutation authority.

## Fail-closed cases

Reconciliation fails closed or withholds nomination when it observes:

- Run/DAG/recovery Task identity disagreement;
- invalid Worker evidence schema or identity;
- conflicting successful Worker commit pairs;
- descendant execution facts after accepted upstream failure;
- released ownership without terminal Worker evidence;
- dependent Task with no accepted integration history;
- stopped integration history;
- dependency missing from the integration head;
- merge history regression/divergence;
- merge attempt without matching successful Worker evidence;
- invalid Git OID shape;
- task or integration commit parent mismatch;
- multi-task queued execution without an execution-base resolver.

None of these conditions is converted into an implicit retry.

## Explicit non-goals

Step 5.4 does not add:

- a second persisted scheduler state machine;
- browser/operator scheduling controls;
- Redis-derived scheduling authority;
- automatic merge-conflict repair;
- Human Gate pause/resume persistence — Step 5.5;
- causal trace correlation — Step 5.6;
- operator recovery/approval UI — Step 5.7;
- chaos/recovery benchmark acceptance — Step 5.8.

## Acceptance tests

Step 5.4 acceptance must prove at least:

1. successful dependency does not rerun;
2. downstream Task waits until accepted integration evidence defines its base;
3. integrated dependency unlocks only the legal downstream frontier;
4. failed dependency blocks descendants without broker publication;
5. ACTIVE owner is never raced;
6. concurrent DAG reconcilers still yield one fresh publication per Task through Step 5.3;
7. queue payload contains no caller-selected Git SHA;
8. multi-task worker fails closed without the base resolver;
9. real two-parent Git merge evidence resolves the dependent base;
10. forged Git integration parentage fails closed;
11. existing V1 deterministic demos remain 5/5 green;
12. full backend and frontend quality gates pass on the final acceptance-ledger head.
