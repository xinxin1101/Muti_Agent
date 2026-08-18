# Step 3.4 — PostgreSQL Persistence

## Purpose

Step 3.4 makes accepted DevFlow runtime evidence durable across process restarts. It does not
move repository truth, success semantics, or scheduling authority into PostgreSQL.

```text
Git / Commit / Worktree
        ↓
validated runtime evidence
        ↓
PostgresEvidenceStore
        ↓
PostgreSQL
        ↓
durable recovery / query / audit
```

The invariant is:

> PostgreSQL persists evidence about runtime decisions; it does not become the authority that
> decides whether repository state is correct.

A row containing `status = 'SUCCEEDED'` is therefore insufficient by itself. Terminal status is
written only through a schema-validated terminal runtime result, and read-back validates that the
persisted status agrees with that terminal result.

## Relational boundary

Step 3.4 intentionally uses four core tables.

### `projects`

Stable repository identity:

- project UUID;
- repository URL;
- default branch;
- creation timestamp.

The repository URL identifies the managed project but PostgreSQL does not store repository file
contents.

### `runs`

One durable execution identity:

- run UUID;
- project UUID;
- exact run base Git commit;
- coarse persistence lifecycle (`RUNNING`, `SUCCEEDED`, `FAILED`);
- terminal `SingleTaskRunResult` JSONB + canonical SHA-256 when applicable;
- start/finish timestamps.

A run may own multiple task rows so the schema remains compatible with the accepted Task DAG.
Step 3.4 does not add distributed worker ownership, lease, heartbeat, or `run_token` fields.

### `tasks`

Immutable TaskContract projection for a run:

- `(run_id, task_id)` composite identity;
- full schema-validated TaskContract JSONB;
- canonical SHA-256 of the contract;
- creation timestamp.

Read-back recomputes the hash, reconstructs `TaskContract`, and verifies that the row `task_id`
matches the payload.

### `evidence_records`

Append-oriented typed runtime evidence:

- run identity;
- optional task identity for run-level evidence;
- caller-owned idempotency key;
- explicit evidence kind;
- optional stage and deterministic sequence;
- evidence schema version;
- JSONB payload;
- canonical SHA-256;
- creation timestamp.

Accepted kinds cover existing structured domain models such as state transitions, Developer,
Verification, Review, Repair, Failure, merge queue, merge conflict, Integration/Human Gate, and
Context fingerprint references.

The payload is not arbitrary prose: `append_evidence()` validates the JSON against the model type
registered for the requested evidence kind before it can be inserted.

## Context privacy / repository-content boundary

Step 3.4 does not persist the selected source-code body contained in a `ContextPacket`.

Instead it persists a `ContextFingerprintReference` containing only:

- task and stage identity;
- canonical packet fingerprint;
- repository HEAD;
- changed-file names;
- selection/snippet/token-estimator strategy names;
- aggregate Context usage.

It intentionally excludes:

- `ContextFile` source text;
- `ContextSnippet.content`;
- complete repository file bodies.

The database can therefore prove which bounded packet identity was used without becoming a second
copy of the repository.

## Transaction semantics

Each public mutating repository method owns an explicit SQL transaction.

### Project creation

`ensure_project()` is idempotent by repository URL. Repeating the same repository/default-branch
identity returns the same project. Reusing the repository URL with conflicting stable metadata is a
persistence conflict rather than an implicit update.

### Run creation

`start_run()` atomically creates:

- one `RUNNING` run row;
- one or more immutable TaskContract rows.

Unknown project IDs, duplicate task IDs, invalid base Git object IDs, or relational conflicts fail
without creating a partially initialized run.

### Evidence append

Each run has a unique `(run_id, evidence_key)` boundary.

A repeated append is idempotent only when kind, task, stage, sequence, schema version, and canonical
payload hash all match. Reusing the same key for different evidence raises
`PersistenceConflictError`.

Terminal runs are append-closed.

This gives later at-least-once worker delivery a safe persistence primitive without implementing
Redis/Dramatiq, lease, heartbeat, or stale-writer fencing in Step 3.4.

## Terminal status authority

Step 3.4 intentionally does not expose a generic API such as:

```text
set_run_status(run_id, "SUCCEEDED")
```

The accepted single-task adapter can finalize only from a validated `SingleTaskRunResult` whose
existing Pydantic invariants already require terminal evidence consistency.

On read-back DevFlow verifies:

1. terminal JSONB canonical SHA-256;
2. `SingleTaskRunResult` schema/invariants;
3. persisted coarse status equals the terminal result state.

If any check fails, the database record is treated as corrupt evidence rather than trusted state.

## Crash and restart semantics

A process may stop after some evidence records have committed but before a terminal result is
written. This is represented deliberately as:

```text
status = RUNNING
terminal_result = NULL
finished_at = NULL
```

Reopening a new `PostgresEvidenceStore` can reconstruct project/run/task identities and all durable
evidence records that were committed before the crash.

Step 3.4 does **not** infer that such a run failed, succeeded, or is safe to resume. Automatic worker
ownership/recovery policy belongs to later lease/heartbeat/run-token steps. Persistence recovery
means "the evidence survived", not "execution was automatically resumed".

## Integrity and typed read-back

Every TaskContract, evidence payload, and terminal result carries a canonical SHA-256 stored next to
its JSONB payload.

Read-back:

- recomputes the hash;
- rejects unknown evidence kinds;
- rejects unsupported evidence schema versions;
- schema-validates the payload into the original domain model;
- verifies task/status identity consistency.

This is an integrity check against accidental/detached database mutation. It is not presented as a
cryptographic signature against a database administrator who can rewrite both payload and hash.

## Migration strategy

Alembic owns the PostgreSQL schema history.

The initial revision creates the four accepted tables, foreign keys, uniqueness boundaries, and
query indexes. CI must prove the migration is reversible by running:

```text
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

before the test suite.

Runtime code does not call `metadata.create_all()` as a silent production migration mechanism.

## Real PostgreSQL acceptance

The Backend Quality workflow provisions a PostgreSQL service and runs persistence integration tests
against it. CI persistence tests may not silently fall back to SQLite.

The required acceptance cases are:

- migration upgrade/downgrade/upgrade succeeds;
- TaskContract and terminal run evidence survive store disposal/recreation;
- multi-task `RUNNING` run state and run-level merge evidence survive restart;
- duplicate identical evidence writes are idempotent;
- duplicate keys with different evidence fail closed;
- terminal runs reject later evidence appends;
- Context persistence stores fingerprint metadata rather than source snippets;
- payload tampering without a matching canonical hash is detected during read-back;
- all pre-Step-3.4 tests remain green.

## Explicitly out of scope

Step 3.4 does not introduce:

- Redis or Dramatiq;
- distributed task dispatch;
- lease ownership;
- heartbeat renewal;
- `run_token` stale-write protection;
- automatic restart/resume policy;
- the later full structured event/log streaming surface;
- frontend/API dashboards;
- embeddings or vector retrieval;
- additional AST/RAG/Agent behavior;
- automatic LLM merge-conflict repair.

Frozen Step 3.4 principle:

> **PostgreSQL can preserve, validate, query, and replay evidence; only the existing deterministic
> runtime and Git evidence are allowed to decide what that evidence means.**
