# Causal Trace Correlation

## Goal

Step 5.6 makes one accepted DevFlow Run explainable as a bounded causal chain without creating a second control plane:

```text
run_id
  ↓
task_id
  ↓
dispatch_id
  ↓
generation
  ↓
Agent / Model Turn
  ↓
Tool Call
  ↓
Verification
  ↓
Review
  ↓
Repair / Integration
```

The trace is a diagnostic projection over accepted runtime facts. It is not scheduler state, retry authority, verification authority, merge authority, terminal-result authority, or publication authority.

Frozen boundary:

> **Trace may explain accepted runtime history; it may not decide, repair, schedule, resume, verify, merge, finalize, or publish that history.**

## Authority model

Step 5.6 deliberately separates authoritative runtime sources from high-resolution diagnostic metadata.

Authoritative sources remain:

- the persisted Run and Task contracts;
- the validated persisted DAG;
- the durable dispatch-attempt ledger;
- PostgreSQL lease/runtime events for accepted generation identity;
- typed/hash-validated verification, review, repair, worker and integration evidence;
- `run_token` fencing for task-scoped writes;
- Git object identity and parentage for code truth.

`TRACE_BATCH` contributes only high-resolution observability that the accepted evidence model did not previously retain, principally Agent/model turns and controlled Tool calls.

No scheduler, reconciler, verifier, Human Gate, completion store, merge queue or GitHub publisher reads `TRACE_BATCH` to authorize work.

## Accepted causal projection

The read model is constructed as:

```text
PersistedRunSnapshot
        +
Task contracts
        +
Durable dispatch attempts
        +
Lease/runtime events
        +
metadata-only TRACE_BATCH evidence
        +
accepted typed execution/integration evidence
        ↓
CausalTraceProjector
        ↓
CausalRunTrace
```

The structural hierarchy is:

```text
RUN
 └─ TASK
     └─ DISPATCH
         └─ GENERATION
             ├─ AGENT_TURN
             │   └─ TOOL_CALL
             ├─ VERIFICATION
             ├─ REVIEW
             ├─ REPAIR
             └─ WORKER_EXECUTION

TASK
 └─ INTEGRATION
```

The projection remains descriptive even when one source is unavailable. Missing optional trace sidecars reduce detail; they do not alter accepted Run truth.

## Correlation identity

The projector does not accept browser-supplied parentage.

Durable correlation is reconstructed from server-owned facts:

- `run_id` comes from the persisted Run;
- `task_id` comes from persisted Task contracts/evidence;
- `dispatch_id` comes from the durable dispatch-attempt ledger and dispatch-scoped evidence;
- `generation` comes from accepted lease/runtime events;
- `TRACE_BATCH` is bound to the same `run_id`, `task_id`, `dispatch_id` and generation;
- typed verification/review/repair evidence is correlated through the existing `dispatch:{dispatch_id}:...` evidence-key namespace;
- worker execution carries its typed `dispatch_id` directly;
- integration spans come from accepted `MERGE_QUEUE_SNAPSHOT` / integration-repair evidence.

Run/Task/Dispatch/Generation projection identities are deterministic. Fine-grained execution spans carry their collector-assigned UUIDs and explicit parent spans.

## Generation cross-check

Generation is especially sensitive because stale execution generations must never regain write authority.

Step 5.6 therefore treats generation as correlation metadata, never as a capability, and cross-checks it against durable lease history:

```text
lease runtime event generation
        ==
TRACE_BATCH generation
```

If the same `dispatch_id` is observed with conflicting generations, projection fails closed as persistence corruption.

This does not cancel, retry or alter the Run. It means only that the diagnostic trace cannot safely claim one causal history from contradictory durable sources.

`run_token` remains the task write capability and is deliberately absent from trace payloads and API responses.

## Metadata-only privacy contract

Step 5.6 intentionally does not persist full model/tool transcripts.

Allowed `AGENT_TURN` metadata includes:

- Agent role;
- model identifier;
- iteration number;
- latency;
- prompt/completion/total token counts;
- finish reason;
- tool-call count.

Allowed `TOOL_CALL` metadata includes:

- Agent role;
- iteration;
- tool name;
- duration;
- success/error status;
- structured Tool error code.

Allowed verification metadata includes:

- verification attempt;
- pass/fail;
- duration.

The default trace payload excludes:

- raw system/user/model prompts;
- model completion text;
- Tool arguments;
- Tool result bodies;
- repository file contents or diffs;
- verifier stdout/stderr bodies;
- credentials, authorization headers and API keys;
- `run_token` values.

The Product trace response declares:

```text
diagnostic_only = true
privacy_mode = METADATA_ONLY
```

This is an architectural contract, not merely a UI convention.

## Agent instrumentation

The production Agent classes accept an optional `TaskTraceCollector`.

Developer:

```text
model response
  → AGENT_TURN
controlled RepositoryToolbox execution
  → TOOL_CALL child of that turn
```

Reviewer:

```text
initial semantic review call
  → AGENT_TURN
invalid structured output repair call
  → separate reviewer.schema_repair_turn
```

Repair:

```text
targeted repair model turn
  → AGENT_TURN
controlled repair Tool execution
  → TOOL_CALL child
```

The single-task Orchestrator adds a `VERIFICATION` span around each deterministic hard-gate invocation.

For compatibility, if no trace collector exists, the Orchestrator calls Developer/Reviewer/Repair with their original call shape. Existing fake Agents and V1 execution paths therefore do not need to know that Step 5.6 exists.

## Worker sidecar boundary

The production queued worker remains the execution authority.

Step 5.6 wraps it with a narrow generation-aware observability extension:

```text
accepted lease grant
        ↓
execute_generation(..., generation=N)
        ↓
generation ContextVar
        ↓
existing QueuedTaskWorker
        ↓
trace-aware local execution backend
        ↓
TaskTraceCollector
        ↓
best-effort TRACE_BATCH append
```

The fenced `run_token` still flows through the original worker API and existing evidence/Git mutation guards.

Generation context is reset after execution.

Most importantly:

> **A TRACE_BATCH persistence failure cannot turn an otherwise successful task into failure and cannot authorize redispatch.**

Trace liveness is intentionally weaker than runtime truth.

## Persistence

`TRACE_BATCH` uses the existing generic typed evidence table. Step 5.6 requires no new database migration.

The trace-aware PostgreSQL store extends the existing evidence store only for the runtime-event source emitted for `TRACE_BATCH`. It inherits the existing:

- canonical payload hashing;
- typed decode validation;
- append-only evidence-key idempotency;
- Run append-close behavior;
- task `run_token` fencing;
- transactional evidence/runtime-event append path.

It does not create a trace-specific database truth or bypass existing persistence authority.

## Read-only Product API

Endpoint:

```text
GET /api/v1/runs/{run_id}/trace
```

The endpoint accepts no query parameters. In particular, the browser cannot supply a `task_id`, `dispatch_id`, generation or parent span to manufacture a causal chain.

Responses are fixed metadata-only projections.

Failure semantics:

- unknown Run → `404`;
- browser-authored selectors/query parameters → `400`;
- bounded projection cannot be completed → `409`;
- contradictory/corrupt persisted source facts → `500`.

None of these responses mutate the Run.

## Boundedness

The projector is intentionally bounded:

- runtime-event scan is capped at 5,000 events;
- runtime events are paged in bounded batches;
- `TaskTraceBatch` span count is bounded;
- `CausalRunTrace` total span count is bounded by schema;
- payload fields are metadata-only rather than unbounded transcript bodies.

If a Run exceeds the diagnostic read budget, the trace endpoint reports the projection as unavailable instead of weakening the bound.

## Failure semantics

Step 5.6 distinguishes runtime truth failure from observability failure.

```text
TRACE_BATCH missing
    → lower trace resolution
    → no execution semantic change

TRACE_BATCH append fails
    → sidecar failure is isolated
    → no success/failure/retry semantic change

TRACE_BATCH conflicts with lease generation
    → trace projection fails closed
    → Run truth remains unchanged

malformed trace/evidence identity
    → trace projection fails closed
    → Run truth remains unchanged
```

The trace is therefore safe to lose but unsafe to fabricate.

## What Step 5.6 does not do

Step 5.6 does not:

- create or advance scheduler state;
- decide READY/BLOCKED Tasks;
- acquire or renew leases;
- create `run_token` values;
- authorize retries or recovery;
- execute verification;
- approve Reviewer decisions;
- authorize Human Gate repair;
- resolve merge conflicts;
- finalize Runs;
- select GitHub publication commits;
- persist raw model/tool transcripts.

## Step 5.7 handoff

Step 5.7 may use this read-only causal chain to make operator diagnosis materially better:

```text
operator sees failure/recovery state
        +
causal trace explains how it happened
        ↓
operator chooses an explicit action
        ↓
existing PostgreSQL / Git / lease / fencing authority
freshly revalidates whether that action is legal
```

The trace may help a human understand *why* recovery is needed. It must never become proof that a recovery mutation is safe.
