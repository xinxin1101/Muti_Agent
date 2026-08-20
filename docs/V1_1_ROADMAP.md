# DevFlow V1.1 Roadmap — Durable Agent Runtime

## Status

- Version line: **V1.1**
- Phase: **Phase 5 — Durable Agent Runtime**
- Current step: **Step 5.1 — Recovery State Classifier**
- V1.0 baseline: `96ec9971624c18b6b64cf5ddf75dff7042952d61`
- V1.0 status: **ACCEPTED / COMPLETE**

## Why V1.1 exists

V1.0 proved that DevFlow can safely execute, verify, review, integrate, publish, observe, and evaluate software-engineering work. The next engineering gap is not another agent role or another dashboard widget. It is **durable execution across process failure, worker loss, delayed messages, long waits, and operator intervention**.

The V1.0 runtime already has most of the prerequisites:

- PostgreSQL typed/hash-validated evidence;
- Redis + Dramatiq transport;
- DB-time task lease and heartbeat;
- monotonic lease generations;
- `run_token` stale-write fencing;
- fenced Git mutations;
- structured runtime events;
- deterministic task/DAG scheduling rules;
- evidence-bound integration and publication.

What it does not yet have is a control loop that can answer, after a crash or restart:

> **What durable work is unfinished, what is still live, what can be resumed from persisted evidence, and what may be safely dispatched again?**

That gap is the focus of V1.1.

## Frozen V1.1 principle

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

Corollaries:

1. Redis queue contents are transport state, not recovery authority.
2. Absence of a worker process is not proof that a task may be replayed.
3. A stale worker generation never regains write authority.
4. Persisted terminal worker evidence must be resumed from, not recomputed by default.
5. Recovery decisions that can mutate runtime state must be freshly revalidated under the same PostgreSQL fencing authority used by normal execution.
6. Human approval must be a durable state transition, not an in-memory callback.
7. Recovery/approval metrics remain observability; they do not decide success.

---

# Phase 5 — V1.1 Durable Agent Runtime

| Step | Capability | Boundary |
| --- | --- | --- |
| 5.1 | Recovery State Classifier | Read durable run/evidence/lease facts and classify recovery candidates; **no enqueue or mutation** |
| 5.2 | Durable Dispatch Attempt Ledger | Persist dispatch intent/attempt identity around broker publication so an `UNOWNED` task is no longer ambiguous |
| 5.3 | Idempotent Task Reconciler | Atomically revalidate recovery eligibility and issue a fresh dispatch/generation only when safe |
| 5.4 | DAG-wide Run Reconciliation | Reconstruct READY/BLOCKED/in-flight/completed work from persisted DAG + evidence and resume multi-task runs deterministically |
| 5.5 | Durable Human Pause / Resume | Persist interruption/approval/rejection state so sensitive operations can pause across process restarts |
| 5.6 | Causal Trace Correlation | Correlate run/task/dispatch/generation/agent/tool/verifier spans without making traces authoritative |
| 5.7 | Operator Recovery / Approval Surface | Read-only recovery diagnosis plus bounded approval/retry controls backed by server-side revalidation |
| 5.8 | Chaos / Recovery Benchmark + V1.1 Acceptance | Kill workers/processes, delay messages, inject stale generations, and prove deterministic recovery boundaries |

---

## Step 5.1 — Recovery State Classifier

### Goal

Build a typed, deterministic read model that converts:

```text
PersistedRunSnapshot
        +
TaskLeaseSnapshot
        +
validated DISPATCH_EVENT / WORKER_EXECUTION evidence
        ↓
TaskRecoveryAssessment
        ↓
RunRecoveryPlan
```

into one of a small set of explicit dispositions.

### Required dispositions

- `NO_ACTION_RUN_TERMINAL`
- `WAIT_ACTIVE_OWNER`
- `RESUME_FROM_TERMINAL_EVIDENCE`
- `REDISPATCH_CANDIDATE_EXPIRED_GENERATION`
- `BLOCKED_UNOWNED_DISPATCH_AMBIGUITY`
- `BLOCKED_RELEASED_EVIDENCE_GAP`

The word **candidate** is intentional. Step 5.1 is not an authorization surface.

### Key Step 5.1 rule

> **Classification is a read projection. Any future redispatch must re-read and lock authoritative PostgreSQL state before mutation.**

This prevents a TOCTOU bug where a recovery screen/classifier says “expired” and a worker heartbeat or terminal evidence arrives before an enqueue action.

### Exit criteria

- every task in the run receives exactly one typed recovery assessment;
- task/lease/run identities must agree;
- terminal worker evidence is parsed from typed persisted payloads rather than logs/messages;
- active leases are never declared recoverable;
- expired leases with accepted terminal execution evidence are resumed from evidence rather than rerun;
- expired leases without terminal execution evidence become redispatch **candidates** only;
- unowned tasks remain blocked because V1.0 lacks durable dispatcher intent;
- released generations without terminal execution evidence remain blocked rather than silently reacquired;
- no `run_token`, credentials, raw prompts, or queue payloads enter the recovery DTO;
- classifier performs no broker call, lease mutation, evidence append, Git mutation, scheduler transition, or Run finalization;
- tests cover all disposition branches plus identity/evidence corruption fail-closed behavior.

---

## Step 5.2 — Durable Dispatch Attempt Ledger

### Problem solved

In V1.0, `DramatiqTaskDispatcher.dispatch()` validates persisted identity and sends a minimal message, but the dispatcher's broker publication is not independently represented as durable runtime intent. Therefore:

```text
RUNNING task
+ UNOWNED lease
```

cannot distinguish:

```text
never dispatched
```

from:

```text
broker accepted message, worker has not acquired lease yet
```

Step 5.2 introduces durable dispatch-attempt identity without making PostgreSQL pretend to know that Redis delivered work.

Expected shape:

```text
DISPATCH_REQUESTED
        ↓
broker send
        ↓
DISPATCH_ENQUEUED / DISPATCH_PUBLISH_FAILED
```

The exact transaction/outbox boundary must be designed before implementation.

---

## Step 5.3 — Idempotent Task Reconciler

Step 5.3 is the first phase allowed to mutate recovery state.

It must:

- use fresh database time;
- lock the relevant Run/Task recovery authority;
- revalidate the Step 5.1 classification inputs;
- allocate a fresh dispatch identity for takeover;
- never reuse an expired `dispatch_id` or `run_token`;
- preserve monotonic generation fencing;
- be safe under multiple concurrent reconcilers;
- make duplicate reconciliation attempts idempotent or conflict explicitly.

A stale Step 5.1 plan must never be sufficient authorization.

---

## Step 5.4 — DAG-wide Run Reconciliation

Extend task-level recovery to the persisted DAG:

```text
validated DAG
+ terminal task evidence
+ live/recoverable task ownership
        ↓
reconstructed scheduling frontier
```

The reconciler must not create a second scheduler truth. READY/BLOCKED remain derived from validated DAG dependencies and accepted task outcomes.

---

## Step 5.5 — Durable Human Pause / Resume

Introduce durable interruption state for operations that require approval, such as selected integration/publication/recovery actions.

Requirements:

- interruption identity is persisted;
- approval/rejection is typed and append-only;
- pending state survives API/worker restart;
- resume revalidates the underlying action preconditions;
- approval does not bypass deterministic verification, scope gates, Git parent checks, or fencing.

---

## Step 5.6 — Causal Trace Correlation

Build a trace model over existing accepted facts:

```text
run_id
  └─ task_id
      └─ dispatch_id
          └─ generation
              ├─ agent/model turn
              ├─ tool call
              ├─ verification
              ├─ review
              └─ repair
```

Trace/span data is diagnostic projection only. It may explain latency and failures; it may not become success authority.

Sensitive prompts/completions remain excluded by default unless a separately designed privacy policy explicitly allows them.

---

## Step 5.7 — Operator Recovery / Approval Surface

Expose bounded product surfaces for:

- recovery classification;
- why a task is waiting/blocked/recoverable;
- durable approval requests;
- approved server-side reconciliation actions.

The browser must never submit arbitrary `run_token`, lease generation, base/head SHA, repository path, or broker payload as authority.

---

## Step 5.8 — Chaos / Recovery Benchmark + V1.1 Acceptance

Required deterministic scenarios should include at least:

1. worker dies while lease is ACTIVE;
2. expired generation attempts a late evidence write;
3. expired generation attempts a late Git mutation;
4. terminal worker evidence persists but downstream completion does not;
5. duplicate reconcilers race to recover the same task;
6. broker publish fails after durable dispatch intent;
7. process restarts while human approval is pending;
8. multi-task DAG resumes without rerunning completed dependencies.

V1.1 is accepted only when recovery behavior is determined by durable evidence/fencing and survives deliberate process/worker failure without stale-result corruption.

---

# Out of scope for V1.1 unless deliberately amended

- arbitrary self-modifying agent policies;
- autonomous force-push/rebase repair;
- multi-region consensus;
- Kubernetes operator/controller implementation;
- distributed transaction claims between PostgreSQL and Redis without an explicit outbox design;
- using trace data, queue depth, or model confidence as task-success authority;
- silently retrying semantic/test failures under the label of crash recovery.

## Resume/interview value

V1.1 is intentionally aimed at the questions that distinguish a toy Agent loop from a production runtime:

- What happens if a worker dies after doing work but before acknowledging completion?
- How do you distinguish retryable infrastructure loss from an accepted task failure?
- How do you prevent two recovery controllers from both taking ownership?
- How do you resume from persisted evidence instead of rerunning expensive model/tool work?
- How do lease, heartbeat, idempotency key, generation, and fencing token differ?
- How do you pause for a human for minutes or hours without holding a process open?
- How do you make observability useful without letting it become control-plane truth?

Those are the engineering boundaries Phase 5 is designed to make concrete and demonstrable.
