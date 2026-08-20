# Step 5.1 Design — Durable Recovery State Classifier

## Status

- Phase: **Phase 5 — V1.1 Durable Agent Runtime**
- Step: **5.1 — Recovery State Classifier**
- Implementation status: **IN PROGRESS**
- V1.0 baseline: `96ec9971624c18b6b64cf5ddf75dff7042952d61`

## Problem

V1.0 has safe ownership transfer primitives but no explicit recovery interpretation layer.

Today a queued worker:

1. receives a minimal `TaskDispatchEnvelope`;
2. acquires a PostgreSQL task lease;
3. receives a fresh `run_token` known only inside the worker process;
4. renews the lease by heartbeat;
5. performs fenced evidence/Git writes;
6. releases the lease after completion.

If that process dies, PostgreSQL can eventually prove that the current generation is `EXPIRED`, and stale writes remain fenced. However the runtime still lacks a deterministic answer to:

> **Should this task wait, resume from already-persisted terminal evidence, become a redispatch candidate, or remain blocked because the durable facts are insufficient?**

Step 5.1 adds that answer as a read-only typed projection.

## Authority boundary

Frozen rule:

> **A recovery classification describes durable facts observed now; it never authorizes a later mutation by itself.**

A future reconciler must revalidate under PostgreSQL locking/fencing immediately before enqueue, lease takeover, state transition, or Git mutation.

The classifier must not trust:

- Redis queue depth;
- Dramatiq in-memory message state;
- browser timers;
- worker process presence;
- log text;
- model output;
- an earlier recovery classification.

## Inputs

### 1. `PersistedRunSnapshot`

Authoritative durable run/evidence projection already validated by `PostgresEvidenceStore.load_run()`:

- Run identity/status;
- persisted TaskContracts;
- typed/hash-validated evidence rows;
- terminal Run result when present.

### 2. `TaskLeaseSnapshot`

DB-time-derived ownership/liveness state:

- `UNOWNED`
- `ACTIVE`
- `EXPIRED`
- `RELEASED`

The snapshot contains generation/dispatch/owner/timestamps but deliberately excludes `run_token`.

### 3. Typed worker/dispatch evidence

For the currently recorded lease `dispatch_id`, the classifier may decode:

- `WORKER_EXECUTION` → `WorkerExecutionEvidence`
- `DISPATCH_EVENT` → `WorkerDispatchEvent`

Evidence is used only after row/payload identity consistency checks.

## Output models

```text
RecoveryDisposition
TaskRecoveryAssessment
RunRecoveryPlan
```

### `RecoveryDisposition`

```text
NO_ACTION_RUN_TERMINAL
WAIT_ACTIVE_OWNER
RESUME_FROM_TERMINAL_EVIDENCE
REDISPATCH_CANDIDATE_EXPIRED_GENERATION
BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
BLOCKED_RELEASED_EVIDENCE_GAP
```

### `TaskRecoveryAssessment`

Carries bounded observability only:

```text
run_id
task_id
disposition
lease_state
lease_generation
lease_dispatch_id
observed_at
worker_execution_status | null
worker_execution_evidence_id | null
reason
```

It must never contain:

```text
run_token
credentials
raw prompt
raw model completion
repository filesystem path
arbitrary broker payload
```

### `RunRecoveryPlan`

```text
run_id
run_status
observed_at
tasks[]
```

All tasks must belong to the same run and must be represented exactly once.

## Classification table

### Run already terminal

```text
PersistedRunStatus != RUNNING
        ↓
NO_ACTION_RUN_TERMINAL
```

A recovery layer must never reopen a completed/failed Run.

### ACTIVE lease

```text
RUNNING
+ lease = ACTIVE
        ↓
WAIT_ACTIVE_OWNER
```

Even if terminal worker evidence has just been written, an ACTIVE owner may still be completing durable projection/finalization. Step 5.1 does not race it.

### EXPIRED lease + terminal worker evidence for the same dispatch

```text
RUNNING
+ lease = EXPIRED
+ WORKER_EXECUTION(dispatch_id=current)
        ↓
RESUME_FROM_TERMINAL_EVIDENCE
```

The expensive/side-effecting task execution has already produced accepted terminal worker evidence. Recovery should continue from that evidence rather than re-run the agent/tool loop.

### EXPIRED lease + no terminal worker evidence for the same dispatch

```text
RUNNING
+ lease = EXPIRED
+ no matching WORKER_EXECUTION
        ↓
REDISPATCH_CANDIDATE_EXPIRED_GENERATION
```

This is only a **candidate**. Step 5.3 must re-read/lock the Task row and prove the generation is still expired immediately before any new dispatch.

### UNOWNED lease

```text
RUNNING
+ lease = UNOWNED
        ↓
BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
```

V1.0 does not durably record dispatcher intent around broker publication. Therefore `UNOWNED` cannot safely distinguish:

```text
never dispatched
```

from:

```text
message accepted by Redis/Dramatiq but worker has not acquired ownership yet
```

Step 5.1 deliberately exposes this gap instead of guessing. Step 5.2 fixes it with a durable dispatch-attempt ledger.

### RELEASED lease + terminal worker evidence

```text
RUNNING
+ lease = RELEASED
+ WORKER_EXECUTION(dispatch_id=current)
        ↓
RESUME_FROM_TERMINAL_EVIDENCE
```

This can occur for multi-task runs or a crash between task-level completion and downstream run/DAG projection.

### RELEASED lease + no terminal worker evidence

```text
RUNNING
+ lease = RELEASED
+ no matching WORKER_EXECUTION
        ↓
BLOCKED_RELEASED_EVIDENCE_GAP
```

`PostgresTaskLeaseStore` intentionally treats RELEASED ownership history as non-reusable. Step 5.1 must not bypass that invariant by silently reacquiring the task.

## Evidence consistency checks

The classifier fails closed when accepted persistence contradicts itself.

Required checks:

1. every persisted task has exactly one lease snapshot;
2. no lease exists for an unknown task;
3. lease `run_id` / `task_id` match the persisted task;
4. all lease snapshots use one observed DB-time basis when supplied as a single plan;
5. `WORKER_EXECUTION` row `run_id/task_id` agree with the decoded payload;
6. decoded `WorkerExecutionEvidence` identity agrees with its evidence row;
7. at most one terminal worker execution evidence exists for one `dispatch_id`;
8. matching `DISPATCH_EVENT` payload identity agrees with its evidence row;
9. a `COMPLETED` dispatch event without matching terminal `WORKER_EXECUTION` is corruption, not a recoverable task;
10. an `UNOWNED` task containing worker-side dispatch/terminal evidence is corruption because worker-side evidence requires acquired fenced ownership.

Use `PersistenceCorruptionError` for contradictions in accepted durable state.

## Concurrency / TOCTOU rule

The first implementation intentionally accepts `PersistedRunSnapshot + TaskLeaseSnapshot[]` as a read projection. These may become stale immediately after classification.

Therefore:

```text
RunRecoveryPlan
    !=
mutation authorization
```

Future Step 5.3 must perform:

```text
candidate plan
      ↓
BEGIN PostgreSQL transaction
      ↓
lock Run + Task authority rows
      ↓
fresh database_time
      ↓
re-read lease/evidence preconditions
      ↓
only then allocate/dispatch recovery attempt
```

No API/UI built in 5.1 may treat `REDISPATCH_CANDIDATE_EXPIRED_GENERATION` as permission to enqueue directly.

## No-mutation guarantee

Step 5.1 code must not call:

- Dramatiq `Actor.send()`;
- `acquire_task_lease()`;
- `renew_task_lease()`;
- `release_task_lease()`;
- `append_evidence()`;
- Run finalization;
- scheduler transitions;
- Git/worktree mutation;
- GitHub publication.

The classifier may only validate/transform already-supplied snapshots.

## Test plan

Required unit tests:

1. terminal Run → `NO_ACTION_RUN_TERMINAL`;
2. ACTIVE → `WAIT_ACTIVE_OWNER`;
3. EXPIRED/no terminal execution → redispatch candidate;
4. EXPIRED/matching terminal execution → resume from evidence;
5. RELEASED/matching terminal execution → resume from evidence;
6. RELEASED/no terminal execution → blocked evidence gap;
7. UNOWNED → blocked dispatch ambiguity;
8. mismatched lease Task identity → fail closed;
9. worker evidence row/payload identity mismatch → fail closed;
10. duplicate terminal worker evidence for same dispatch → fail closed;
11. COMPLETED dispatch event without terminal execution → fail closed;
12. UNOWNED with worker-side evidence → fail closed;
13. `run_token` is absent from serialized recovery DTOs.

Existing lease/fencing tests remain unchanged and authoritative for production ownership semantics.

## Step 5.1 acceptance

Step 5.1 is accepted only when:

- design and implementation preserve all existing lease/fencing behavior;
- no migration is required;
- new classifier tests pass;
- full Backend Quality remains green;
- existing V1.0 deterministic demo suite remains 5/5 green;
- final progress ledger records Phase 5 as IN PROGRESS, Step 5.1 as ACCEPTED, Step 5.2 as NEXT;
- PR merges only from an exact head that has passed required quality checks.

Frozen Step 5.1 boundary:

> **Recovery classification may identify safe candidates from durable evidence; only a future freshly fenced reconciler may act on them.**
