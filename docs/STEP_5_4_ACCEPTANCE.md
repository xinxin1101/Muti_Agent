# Step 5.4 Acceptance — DAG-wide Run Reconciliation

Status: **ACCEPTED / COMPLETE**

## Frozen boundary

> **Run reconciliation may reconstruct the scheduling frontier from validated DAG and accepted terminal task facts; it may not create a second scheduler truth.**

Step 5.4 is accepted because recovery now reconstructs one legal DAG frontier from durable authorities and delegates actual recovery dispatch to the already-accepted Step 5.3 per-Task reconciler. No second mutable scheduler state, browser state, Redis state, or stale recovery plan can authorize execution.

## Accepted architecture

```text
PersistedRunSnapshot
        +
hash-validated PersistedDAGSnapshot
        +
accepted WORKER_EXECUTION evidence
        +
fresh DB-time lease facts
        +
accepted cumulative MERGE_QUEUE_SNAPSHOT history
        ↓
RecoveryStateClassifier
        +
TaskDAG ready/blocked semantics
        +
EvidenceBoundTaskExecutionBaseResolver
        ↓
DAGRunReconciliationPlan
        ↓
RECONCILE_CANDIDATE tasks only
        ↓
Step 5.3 IdempotentTaskReconciler
        ↓
fresh locked PostgreSQL mutation authorization
```

The plan is a read/nomination projection. It is deliberately allowed to become stale because Step 5.3 performs the authoritative fresh locked check before creating or publishing a dispatch attempt.

## Accepted frontier semantics

The following states are derived on every pass and are not independently persisted as scheduler truth:

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

Accepted successful `WORKER_EXECUTION` evidence makes a Task completed for DAG dependency purposes. Accepted failed Worker evidence makes it failed. Existing `TaskDAG.ready_task_ids()` and `TaskDAG.blocked_task_ids()` remain the dependency authority.

Completed dependencies are never rerun merely because scheduler/controller process state was lost.

## Dependency-aware execution-base hardening

Step 5.4 found and closed a pre-existing multi-task execution gap: queued workers previously used `PersistedRunSnapshot.base_commit` for every Task. That is valid for roots but can execute a dependent Task from a tree that does not contain its successfully completed dependency.

The accepted rule is now:

```text
DAG READY
    !=
safe to execute immediately

DAG READY
        +
accepted dependency integration
        +
evidence-bound Git execution base
        =
eligible for Step 5.3 nomination
```

Root Tasks use the frozen Run base. Dependent Tasks require accepted cumulative `MERGE_QUEUE_SNAPSHOT` evidence proving that all direct dependencies are present in the current integration head.

The queue still carries only:

```text
dispatch_id
run_id
task_id
```

It never carries a caller-selected base SHA, integration SHA, branch, lease generation, or `run_token`.

A multi-task queued worker without the evidence-bound execution-base resolver now fails before backend/Git execution or worker evidence publication.

## PostgreSQL / Git authority cross-check

`EvidenceBoundTaskExecutionBaseResolver` validates persisted integration history against successful Worker commit pairs and deterministic DAG order.

Among the accepted checks:

- Worker and merge evidence must be typed, identity-bound, and hash-valid through normal persistence loading;
- evidence history is read in durable `EvidenceRow.id` order;
- successful Worker evidence for one Task cannot define conflicting commit pairs;
- merge attempts must refer to known DAG Tasks and respect topological order;
- direct dependencies must already be integrated before a dependent merge attempt;
- root task commits start from the frozen Run base;
- dependent task commits start from a prior integration head that already contains their dependencies;
- merge snapshot history may extend but cannot regress or diverge;
- one Run cannot silently switch integration refs;
- stopped conflict history cannot silently advance;
- merge attempts must match accepted successful Worker `(task_base_commit, task_commit)` pairs.

Production worker composition injects a managed Git workspace resolver, so persisted code-state claims are reproduced from Git before a dependent base is accepted.

Exact Git rules:

```text
parents(task_commit) == (task_base_commit,)

parents(integration_commit) == (
    previous_integration_commit,
    task_commit,
)
```

A regression test creates a real two-parent Git merge and proves the dependent base resolves to that integration commit. A second test provides syntactically valid but false integration parentage and proves resolution fails closed as `PersistenceCorruptionError`.

## Step 5.3 remains mutation authority

Only a Task derived as `RECONCILE_CANDIDATE` is delegated to Step 5.3.

Eligibility requires:

```text
Run RUNNING
+
DAG-ready
+
no accepted terminal Worker evidence
+
UNOWNED or EXPIRED ownership
+
trusted execution base
```

Step 5.4 does not directly create dispatch rows or call the broker. Step 5.3 freshly locks/revalidates Run, Task, lease, terminal evidence, and dispatch history before publication.

Concurrent Run reconcilers may therefore nominate the same Task without creating a second scheduler lock domain. PostgreSQL Task-level linearization in Step 5.3 still provides the exactly-one fresh publication property per Task.

## Failure behavior accepted

Step 5.4 does not turn ambiguity into retry authorization.

It waits or fails closed for:

- active ownership;
- released ownership with missing terminal evidence;
- successful dependency not yet represented in accepted integration history;
- unresolved/stopped integration history;
- upstream accepted failure;
- Run/DAG/recovery identity disagreement;
- invalid or conflicting Worker evidence;
- inconsistent merge history;
- merge attempts without matching successful Worker evidence;
- impossible downstream execution facts after accepted upstream failure;
- invalid Git object identifiers;
- Git parentage that cannot reproduce persisted integration evidence;
- multi-task worker execution without a dependency-aware base resolver.

## Concurrency evidence

PostgreSQL integration tests cover two DAG-wide reconcilers observing two independent ready roots concurrently.

Both read projections may nominate A and B, but after Step 5.3 delegation the durable result is:

```text
A -> exactly one fresh ENQUEUED dispatch attempt
B -> exactly one fresh ENQUEUED dispatch attempt
```

No Run-global mutable scheduler state was introduced to manufacture this property.

## Implementation acceptance evidence

Exact implementation head before documentation/workflow ledger commits:

`16be10b49a563429f459e337410bcb2c94a1d3ae`

Backend Quality on that head:

- PostgreSQL + Redis: **PASS**;
- Alembic `0001 → 0007 → base → 0007`: **PASS**;
- no Step 5.4 schema migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **393 passed in 35.07s**.

After adding the design document and both workflow path gates, exact branch head:

`04b8d735b218d17196c766a725985f6115c3fe9f`

also passed:

- Backend Quality: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **393 passed in 39.44s**;
- Frontend Quality locked install/typecheck/lint/tests/build: **PASS**.

The acceptance/progress ledger paths are now included in both Backend and Frontend workflow filters. The final ledger head is therefore required to independently pass both workflows before PR #37 may leave Draft and merge.

## Merge review

PR #37 merge review confirmed:

- production worker composition injects the managed-Git execution-base resolver;
- persisted evidence loads in deterministic ascending evidence-id order;
- no new scheduler persistence table or mutable scheduling state exists;
- broker payload remains SHA-free and token-free;
- Step 5.3 remains the only recovery dispatch publication authority;
- Step 5.5 Human Pause/Resume behavior was not smuggled into 5.4;
- unresolved review threads at acceptance review: **0**.

## Explicitly deferred

Step 5.4 does not implement:

- durable Human Pause / Resume — Step 5.5;
- causal trace correlation — Step 5.6;
- operator recovery/approval UI — Step 5.7;
- chaos/recovery benchmark and V1.1 final acceptance — Step 5.8.

## Next authority transition

Step 5.5 may introduce durable pause/resume facts, but they must compose with this accepted frontier rather than becoming a browser-owned or in-memory scheduler stop flag.

The next question is no longer:

```text
Which DAG tasks can recovery safely nominate?
```

It is:

```text
When execution is intentionally paused for a Human decision,
what durable fact prevents recovery from treating that pause as abandoned work,
and what accepted decision is allowed to resume it?
```
