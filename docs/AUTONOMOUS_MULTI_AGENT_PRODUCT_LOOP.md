# Phase 6 — Autonomous Multi-Agent Product Loop

## Goal

Phase 6 turns the already-accepted DevFlow runtime capabilities into one user-facing product loop:

```text
repository + natural-language requirement
        ↓
bounded Planner proposal
        ↓
validated + persisted TaskDAG
        ↓
durable root dispatch
        ↓
parallel generation-bound workers
        ↓
topological evidence-bound integration
        ↓
durable downstream reconciliation
        ↓
Human Gate / bounded conflict repair when required
        ↓
terminal evidence-bound Run result
        ↓
Diff / GitHub Draft PR projection
```

The product boundary remains unchanged:

> **Agents propose; evidence decides.**

The browser may request work and record an explicit Human decision. It may not supply Git SHAs, TaskContracts, dependency edges, dispatch IDs, lease generations, `run_token` values, repository paths, merge results, or publication source commits as runtime authority.

---

## Step 6.1 — Natural-language Run entry

The browser-facing New Run path accepts only:

- a persisted Project / repository identity;
- a natural-language requirement.

The Product API freezes the managed repository HEAD before planning and passes only bounded repository context to the Planner.

The original V1 TaskContract endpoint remains available for compatibility and deterministic benchmark coverage; the autonomous path is separate and does not weaken that interface.

---

## Step 6.2 — Planner → validated TaskDAG

`MultiTaskPlannerAgent` proposes a structured multi-task plan. Its output is not scheduling truth until it passes typed validation.

Accepted boundaries include:

- bounded task count and output size;
- Pydantic TaskContract / TaskDAG validation;
- unique task identities;
- existing dependency identities;
- acyclic topology;
- bounded schema-repair attempts for malformed model output;
- server-owned TaskContract scope, dependency and verification fields after validation.

Planner text never authorizes execution or success.

---

## Step 6.3 — Persist Run + DAG before dispatch

The Product API persists the validated DAG and frozen Run base before publishing any root task.

```text
Planner proposal
    ↓ validate
TaskDAG
    ↓ persist transactionally
PostgreSQL Run + TaskContracts + dependencies + DAG hash + base commit
    ↓
initial root dispatch
```

After persistence, the immutable persisted DAG is the dependency authority. A later Planner response cannot mutate the active Run topology.

---

## Step 6.4 — Durable DAG Controller

`DurableMultiAgentRunController` is reconciliation-driven rather than a resident in-memory workflow loop.

Each bounded `advance(run_id)` reconstructs current state from:

- the persisted Run and TaskDAG;
- typed terminal worker evidence;
- durable dispatch / lease state;
- accepted cumulative merge history;
- managed Git parent relationships;
- durable Human Gate / repair evidence where applicable.

It then performs a bounded action and persists the result. Process restarts do not require an in-memory callback or scheduler object to preserve progress.

Downstream dispatch remains delegated to the accepted Step 5.4 / Step 5.3 reconciliation authority rather than creating a second dispatcher.

---

## Step 6.5 — Parallel execution + automatic integration

Independent READY roots may be published concurrently. Worker code changes remain isolated in generation-bound Git worktrees and produce accepted task commits.

The Controller reuses the existing topological integration architecture:

```text
successful WorkerExecutionEvidence
        ↓
GenerationBoundWorktreeView
        ↓
RepairAwareTopologicalMergeQueue
        ↓
Git parent / branch / commit revalidation
        ↓
accepted cumulative MergeQueueSnapshot
```

A dependent Task becomes dispatchable only after accepted integration evidence proves its execution base contains every direct dependency. Queue payloads still contain only stable dispatch identity (`dispatch_id`, `run_id`, `task_id`); code-base authority is resolved server-side.

---

## Step 6.6 — Durable Human Pause / Resume + bounded repair

A merge conflict does not become an Agent-repair instruction by itself.

The accepted flow is:

```text
CONFLICT
  ↓ structured MergeConflictEvidence
Integration / Human Gate
  ↓ durable typed decision
AUTHORIZE_REPAIR
  ↓ exact conflict + policy revalidation
bounded Developer repair on classified conflict paths only
  ↓ original TaskContract deterministic verification
INTEGRATION_REPAIR evidence
  ↓ Git CAS
REPAIRED integration history
```

Human approval authorizes a bounded action only. It cannot bypass scope enforcement, deterministic verification, Git parent validation, persisted evidence binding, lease authority, or `run_token` fencing.

### Crash-safe repair object liveness

The repair commit is protected before PostgreSQL records it:

```text
repair commit
    ↓
server-owned staging ref
refs/devflow/integration-repairs/<run>/<conflict-marker>
    ↓
persist INTEGRATION_REPAIR
    ↓
CAS integration ref
    ↓
archive Human Gate refs
    ↓
delete staging ref
```

The staging ref is **not** a new runtime truth source. PostgreSQL typed repair evidence remains the authority for recovery. The ref only keeps the already-created Git object alive across the database-to-CAS crash window.

Acceptance explicitly expires reflogs and runs `git gc --prune=now` after a simulated process death before integration CAS. A fresh repair service must recover the exact persisted repair commit without calling the Agent again, advance the integration ref, remove the staging ref, and reconstruct `INTEGRATED → CONFLICT → REPAIRED` history.

`REPAIRED` integration is also revalidated at all product exits that consume it:

- downstream execution-base resolution;
- terminal Run success;
- Integration Diff projection;
- GitHub Draft PR publication.

A Git metadata marker alone is insufficient; matching typed `INTEGRATION_REPAIR` and `AUTHORIZE_REPAIR` evidence is required.

---

## Step 6.7 — Deterministic product E2E

The Phase 6 acceptance test exercises one coherent three-task product path with a real temporary Git repository and real PostgreSQL stores:

```text
Requirement
    ↓
root-a ─┐
        ├─ parallel READY roots
root-b ─┘
    ↓
real generation-bound task commits
    ↓
automatic topological integration
    ↓
dependent becomes the only legal reconciliation candidate
    ↓
durable dependent dispatch
    ↓
dependent task commit based on accepted integration head
    ↓
final integration
    ↓
SUCCEEDED persisted Run
    ↓
Integration Diff
    ↓
evidence-selected GitHub Draft PR intent
    ↓
deterministic remote publication projection
```

The E2E intentionally keeps external nondeterminism out of CI:

- the Planner is a fixed deterministic TaskDAG producer;
- the broker actor records stable envelopes without Redis delivery timing;
- the GitHub publisher returns validated deterministic remote facts.

The runtime authorities themselves are real: Git worktrees / commits / merge history, PostgreSQL evidence, dispatch ledger, lease/run-token fencing, Step 5.4 DAG reconciliation, Step 5.3 idempotent dispatch preparation, repair-aware completion validation, Diff reconstruction and publication persistence.

---

## Accepted product boundary

Phase 6 establishes this user-facing invariant:

> **A user may start DevFlow with a repository and a natural-language requirement; every later scheduling, execution, integration, repair, success, diff and publication decision is reconstructed from server-owned validated facts rather than browser or Agent claims.**

Phase 6 does not claim that all post-V1.1 hardening is complete. Causal trace correlation, the broader operator recovery surface, and the full chaos/recovery benchmark remain separate Phase 5 backlog items unless independently accepted.