# Step 3.5 — Redis + Dramatiq Workers

## Purpose

Step 3.5 moves DevFlow task execution across a process boundary without changing who owns runtime
truth.

```text
Validated persisted Run / Task
          ↓
DramatiqTaskDispatcher
          ↓
TaskDispatchEnvelope
(dispatch_id, run_id, task_id)
          ↓
Redis
          ↓
Dramatiq actor
          ↓
QueuedTaskWorker
          ↓
reload + validate PostgreSQL identity
          ↓
managed Git repo / isolated task worktree
          ↓
existing SingleTaskOrchestrator
          ↓
Git task commit + typed PostgreSQL evidence
```

The queue is transport. It is not the scheduler, the code source of truth, or a success signal.

## Trust boundaries

### Redis message

The only accepted queue payload is:

```text
dispatch_id
run_id
task_id
```

It intentionally excludes:

- TaskContract bodies;
- repository source or diffs;
- workspace paths supplied by callers;
- PostgreSQL credentials;
- SiliconFlow or GitHub credentials;
- prompts, ContextPacket source snippets, or model output.

The worker treats queue fields only as identity references. It reloads the Run/Task through
`PostgresEvidenceStore.load_run()`, which re-validates persisted hashes and typed models.

### PostgreSQL

PostgreSQL remains durable runtime evidence. The worker records typed dispatch/execution evidence
using the accepted `(run_id, evidence_key)` idempotency boundary.

New evidence kinds are:

- `DISPATCH_EVENT`;
- `WORKER_EXECUTION`.

No Step 3.5 Alembic migration is required because `evidence_records.kind` is already a versioned
string discriminator and the JSONB payload is schema-validated on read-back.

### Git

The managed repository and task worktree remain authoritative for code state.

The production resolver uses:

```text
<DEVFLOW_WORKSPACE_ROOT>/repos/<project_id>
<DEVFLOW_WORKSPACE_ROOT>/worktrees/<run_id>/...
```

Queued worktree/branch identity is derived from both the persisted `run_id` and the TaskContract
`task_id`. This preserves the Phase 2 rule that a duplicate execution attempt inside the same run
collides fail-closed, while allowing a later independent run to reuse the same logical task id
without colliding with the deliberately retained Git branch from an earlier run.

The queued backend also freezes the persisted Run `base_commit` rather than silently replacing it
with the managed repository's newer HEAD if the message waited in Redis. The frozen commit must
still exist and must remain an ancestor of the current managed-repository HEAD; divergence or
history replacement fails closed. Successful task commits therefore descend from the Run's
original immutable base even when the managed checkout has advanced linearly since dispatch.

A successful queued execution is not complete merely because the actor returned. The accepted
runtime must succeed and the task worktree must be frozen as an actual Git commit.

## Dispatch semantics

`DramatiqTaskDispatcher` performs a fresh persistence read immediately before enqueue:

1. run must still be `RUNNING`;
2. task id must belong to the persisted run;
3. the dispatcher creates or accepts a UUID `dispatch_id`;
4. only the minimal envelope is sent to the actor;
5. broker enqueue failure is surfaced as `TaskDispatchBrokerError`.

The dispatcher deliberately does not pretend Redis enqueue and PostgreSQL append are one atomic
transaction. Worker-side durable evidence begins when the message is actually received.

## Worker semantics

`QueuedTaskWorker`:

1. reloads and validates the persisted run;
2. rejects non-`RUNNING` runs;
3. resolves exactly one persisted TaskContract for the message task id;
4. appends a `RECEIVED` dispatch event;
5. invokes the queued execution backend;
6. persists existing state/developer/verification/review/repair/failure runtime evidence under
   dispatch-scoped evidence keys;
7. appends `WORKER_EXECUTION`;
8. appends a terminal `COMPLETED` dispatch event;
9. for a single-task persisted run, finalizes only when queued execution status and the nested
   terminal `SingleTaskRunResult` agree.

A failure after the runtime itself reported success but before a Git task commit is created is
therefore **not** persisted as a successful terminal run.

Multi-task project-run finalization remains an orchestration/integration concern; Step 3.5 does not
turn one task actor into the owner of the entire DAG run.

## Retry and redelivery boundary

Dramatiq supports automatic actor retries, but the DevFlow task actor explicitly configures:

```text
max_retries = 0
```

This is required in Step 3.5 because lease/heartbeat ownership and `run_token` stale-writer fencing
do not exist yet. Automatically replaying a repository-mutating task would otherwise pretend that
message transport owns execution safety.

This does **not** mean duplicate delivery is impossible. Broker/worker failures can still cause a
message to be seen again.

Current fail-closed behavior is therefore:

- persisted evidence keys reject conflicting reuse;
- terminal single-task runs reject later worker execution;
- an already-created task worktree/branch collides rather than being silently reused;
- DevFlow does not claim exactly-once task execution.

Safe worker-death recovery is intentionally completed in:

```text
Step 3.6 — Lease + heartbeat
Step 3.7 — run_token stale-write protection
```

## Testing boundary

Step 3.5 acceptance requires both isolated and real-service tests:

- `StubBroker` dispatcher/actor tests for minimal payload and `max_retries=0`;
- real Git worktree test proving queued execution leaves the base workspace unchanged and commits
  the successful task branch;
- duplicate worktree identity test proving replay in the same run fails closed;
- distinct-run test proving the same logical `task_id` receives a distinct retained Git identity;
- delayed-execution test proving an advanced managed HEAD does not replace the persisted Run base;
- real Redis + Dramatiq Worker + PostgreSQL round trip with a fake execution backend, proving the
  process/thread queue boundary without paid model calls;
- all earlier PostgreSQL, sandbox, Context, Agent, DAG, worktree, merge/conflict, and Human Gate
  regressions remain green.

## Explicit non-goals

Step 3.5 does not add:

- lease expiry;
- heartbeat renewal;
- `run_token`;
- stale-worker fencing;
- automatic worker-death recovery;
- exactly-once claims;
- a new scheduler;
- frontend/SSE behavior;
- embeddings/vector retrieval;
- new AST/RAG/Agent behavior;
- automatic LLM merge-conflict repair.

Frozen principle:

> **Queue delivery authorizes an execution attempt; Git and validated runtime evidence still decide
> what actually happened.**
