# Phase 5 / Step 5.1 Acceptance — Recovery State Classifier

## Status

**ACCEPTED / COMPLETE**

- Version line: **V1.1**
- Phase: **Phase 5 — Durable Agent Runtime**
- Step: **5.1 — Recovery State Classifier**
- V1.0 baseline: `96ec9971624c18b6b64cf5ddf75dff7042952d61`
- Final implementation head before this acceptance ledger: `b4503968310cdc3e5e0cd27bfa062abfc5b253f9`
- Pull request: **#34**
- Next step: **5.2 — Durable Dispatch Attempt Ledger**

## Frozen principle

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

Step 5.1 is intentionally classification-only. A recovery diagnosis is not mutation authorization.

## Accepted architecture

```text
PersistedRunSnapshot
        +
TaskLeaseSnapshot
        +
validated WORKER_EXECUTION / DISPATCH_EVENT evidence
        ↓
RecoveryStateClassifier
        ↓
TaskRecoveryAssessment
        ↓
RunRecoveryPlan
```

`RecoveryInspector` composes the existing PostgreSQL evidence and lease read models. It does not enqueue work, mutate a lease, append evidence, finalize a Run, advance scheduler state, mutate Git/worktrees, or publish GitHub state.

## Accepted dispositions

- `NO_ACTION_RUN_TERMINAL`
- `WAIT_ACTIVE_OWNER`
- `RESUME_FROM_TERMINAL_EVIDENCE`
- `REDISPATCH_CANDIDATE_EXPIRED_GENERATION`
- `BLOCKED_UNOWNED_DISPATCH_AMBIGUITY`
- `BLOCKED_RELEASED_EVIDENCE_GAP`

The word **candidate** is authoritative: `REDISPATCH_CANDIDATE_EXPIRED_GENERATION` is not permission to call Dramatiq or acquire a new lease. Step 5.3 must perform a fresh, locked PostgreSQL revalidation before any takeover or enqueue.

## Accepted safety semantics

### Active owner

An `ACTIVE` lease always classifies as `WAIT_ACTIVE_OWNER`. Recovery never competes with a currently live owner.

### Terminal worker evidence

Accepted terminal `WORKER_EXECUTION` evidence is resumed from instead of being recomputed merely because the recorded lease is expired or released.

### Expired generation without terminal evidence

An expired generation with no accepted terminal worker evidence becomes a redispatch **candidate only**. No broker call or new generation is allocated in Step 5.1.

### Unowned task

An `UNOWNED` task remains blocked because V1.0 has no durable dispatcher-intent ledger. The classifier refuses to guess whether work was never dispatched or whether a broker-accepted message has not yet acquired a lease. Step 5.2 closes this ambiguity.

### Released generation without terminal evidence

A released generation cannot be silently reacquired or rewritten. Missing terminal evidence after release is surfaced as `BLOCKED_RELEASED_EVIDENCE_GAP`.

## Fail-closed corruption checks

Step 5.1 rejects inconsistent durable facts including:

- Run/task/lease identity disagreement;
- missing or unexpected task lease snapshots;
- lease snapshots taken at inconsistent database observation times;
- worker payload Run or Task identity disagreement with its evidence row;
- duplicate terminal `WORKER_EXECUTION` evidence for one dispatch;
- duplicate worker dispatch phases for one dispatch;
- a `COMPLETED` dispatch event without terminal `WORKER_EXECUTION` evidence;
- an `UNOWNED` task carrying worker-side execution/dispatch evidence.

The `UNOWNED + worker-side evidence` invariant is checked even for terminal Runs so terminal state cannot hide persistence corruption.

## Sensitive-data boundary

Recovery DTOs intentionally exclude:

- `run_token`;
- credentials;
- raw prompts or model completions;
- repository/worktree paths as authority;
- arbitrary broker payloads.

## PostgreSQL integration proof

`backend/tests/test_recovery_inspector_postgres.py` constructs a real persisted RUNNING Run through `PostgresEvidenceStore`, reads lease state through `PostgresTaskLeaseStore`, and invokes `RecoveryInspector.inspect_run()`.

The initial task is classified as:

```text
UNOWNED
    ↓
BLOCKED_UNOWNED_DISPATCH_AMBIGUITY
```

The test then verifies that recovery inspection did not change the persisted Run snapshot, runtime event history, lease generation, owner/dispatch identity, or lease lifecycle timestamps.

## Implementation-head CI evidence

Exact implementation head:

`b4503968310cdc3e5e0cd27bfa062abfc5b253f9`

Backend Quality run **32342823607**:

- PostgreSQL + Redis services: **PASS**;
- Alembic upgrade → downgrade base → upgrade: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- V1.0 deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **377 passed in 35.41s**.

No database migration is required by Step 5.1 because the feature is a read-only classification layer over already accepted durable state.

## Acceptance gate for this ledger commit

The acceptance/progress ledger commit itself must independently pass the workflow-triggering Backend Quality and Frontend Quality gates before PR #34 is merged. If either workflow fails, this acceptance status is invalid until the branch is repaired and revalidated.

## Out of scope / deferred

Step 5.1 deliberately does not implement:

- durable dispatch intent/attempt state — **Step 5.2**;
- automatic redispatch or takeover — **Step 5.3**;
- DAG-wide reconciliation — **Step 5.4**;
- durable Human pause/resume — **Step 5.5**;
- causal trace correlation — **Step 5.6**;
- operator recovery controls — **Step 5.7**;
- chaos/recovery benchmark — **Step 5.8**.

## Final Step 5.1 boundary

> **Step 5.1 may determine what durable state appears recoverable, waiting, resumable, blocked, or terminal; it may not turn that diagnosis into a runtime mutation.**
