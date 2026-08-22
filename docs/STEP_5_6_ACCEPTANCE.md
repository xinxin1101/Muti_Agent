# Step 5.6 Acceptance — Causal Trace Correlation

Status: **ACCEPTED / COMPLETE**

## Frozen boundary

> **Trace may explain accepted runtime history; it may not decide, repair, schedule, resume, verify, merge, finalize, or publish that history.**

Step 5.6 is accepted because DevFlow can reconstruct a bounded queryable causal chain from Run to Agent/Tool/verification/review/repair/integration without promoting trace metadata into a second scheduler, retry system, success verdict, or Git authority.

The implementation head and the complete candidate ledger containing design/progress/workflow-gate changes have both independently passed strict Backend and Frontend quality gates. The accepted-state head created by this status transition must also pass both workflows before PR #39 may leave Draft.

## Accepted architecture

```text
Persisted Run / Tasks / DAG
        +
Durable dispatch attempts
        +
Lease/runtime events
        +
metadata-only TRACE_BATCH
        +
accepted typed runtime evidence
        +
accepted integration evidence
        ↓
CausalTraceProjector
        ↓
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
     └─ INTEGRATION
```

`CausalRunTrace` is a read model. No accepted runtime component consumes it to authorize mutations.

## Authority matrix

| Fact | Authority | Trace role |
| --- | --- | --- |
| Run / Task identity | PostgreSQL typed persistence | render correlation |
| DAG dependency truth | validated persisted `TaskDAG` | render task hierarchy only |
| Dispatch identity/outcome | durable dispatch-attempt ledger | create dispatch spans |
| Worker generation | lease/runtime events | create/cross-check generation spans |
| Task write capability | current `run_token` fencing | **not persisted or returned** |
| Verification result | deterministic verifier typed evidence | render verification spans |
| Review decision | typed Reviewer evidence | render review spans |
| Repair result | typed repair evidence | render repair spans |
| Worker success/failure | `WORKER_EXECUTION` evidence | render worker span |
| Integration result | merge/integration evidence + Git | render integration spans |
| Agent/model/tool detail | metadata-only `TRACE_BATCH` | diagnostic enrichment only |
| Code truth | Git objects and parentage | no replacement authority |

Trace cannot override any authority in this table.

## Metadata-only privacy acceptance

Accepted `TRACE_BATCH` metadata includes only bounded diagnostic fields such as:

- Agent role and model identifier;
- model iteration;
- prompt/completion/total token counters;
- model latency and finish reason;
- Tool name, status, duration and structured error code;
- deterministic verification attempt, pass/fail and duration.

The trace intentionally excludes:

- raw prompts;
- raw model completions;
- Tool arguments;
- Tool result bodies;
- repository file contents or diffs;
- verifier stdout/stderr bodies;
- credentials/API keys;
- `run_token`.

The Product response is structurally marked:

```text
diagnostic_only = true
privacy_mode = METADATA_ONLY
```

Tests serialize real collector output and prove secret completion text, Tool content and Tool mutation arguments do not appear.

## Production Agent instrumentation accepted

Developer, Reviewer and Repair now accept an optional `TaskTraceCollector`.

Production-class regression coverage proves one generation collector receives:

```text
DEVELOPER model turn
DEVELOPER Tool call
DEVELOPER completion turn
REVIEWER initial turn
REVIEWER schema-repair turn
REPAIR turn
```

Reviewer schema-repair calls are separate turns instead of being hidden inside one aggregate review record.

The Orchestrator records each deterministic verification invocation as its own span.

Compatibility was hardened explicitly: when `trace is None`, the Orchestrator calls Developer/Reviewer/Repair with their original argument shape. Existing fake Agents and pre-Step-5.6 runtime tests therefore remain valid without accepting a new keyword argument.

No `TypeError` fallback is used, so an actual Agent implementation bug cannot be mistaken for legacy-signature compatibility.

## Dispatch / generation correlation accepted

Correlation uses durable server-owned identity:

```text
run_id
  → task_id
  → dispatch_id
  → generation
```

The browser does not supply these edges.

The projector cross-validates `TRACE_BATCH.generation` against durable lease runtime events. Conflicting generations for the same dispatch fail closed as `PersistenceCorruptionError`.

Generation remains correlation metadata, not write authority. `run_token` remains the fenced capability and does not enter the trace.

## Worker-sidecar non-authority accepted

Production worker composition adds a generation-aware trace sidecar without replacing the existing worker execution path.

Accepted behavior:

- the existing `QueuedTaskWorker` still executes the task;
- generation is introduced through a narrow `execute_generation` compatibility extension;
- the existing fenced `run_token` continues through the original execution/evidence/Git mutation boundary;
- collector context is reset after execution;
- `TRACE_BATCH` is written through the existing typed/hash/fenced PostgreSQL evidence path;
- a trace append failure is caught inside the sidecar and cannot convert task success into failure;
- a trace append failure cannot authorize recovery or redispatch.

This establishes the intended asymmetry:

> **Trace is safe to lose but unsafe to fabricate.**

## Persistence acceptance

No Step 5.6 migration is required.

`TRACE_BATCH` uses the existing generic evidence schema and typed evidence decoder. `TraceAwarePostgresEvidenceStore` subclasses the existing `PostgresEvidenceStore` only to provide the observability event-source mapping for `TRACE_BATCH`.

It inherits existing:

- canonical payload hashing;
- typed decode validation;
- append-only evidence-key idempotency;
- terminal Run append-close behavior;
- task `run_token` fencing;
- evidence/runtime-event transaction handling.

PostgreSQL integration coverage proves a fenced `TRACE_BATCH` can be appended and reloaded and that its corresponding runtime event retains dispatch/generation correlation and metadata-only flags.

## Typed evidence and integration correlation accepted

The projector correlates existing durable execution evidence rather than copying its contents into trace-specific truth:

- `VERIFICATION_RESULT` → `VERIFICATION`;
- `REVIEW_DECISION` → `REVIEW`;
- `REPAIR_RUN` → `REPAIR`;
- `WORKER_EXECUTION` → `WORKER_EXECUTION`;
- `FAILURE_REPORT` → `FAILURE`;
- `INTEGRATION_REPAIR` → integration-repair `REPAIR`;
- latest accepted `MERGE_QUEUE_SNAPSHOT` attempts → `INTEGRATION`.

Dispatch-scoped evidence is correlated through the existing `dispatch:{dispatch_id}:...` evidence namespace. Worker execution carries its typed dispatch identity directly.

Malformed identities or impossible parentage fail trace projection rather than silently inventing an edge.

## Read-only Product API accepted

Endpoint:

```text
GET /api/v1/runs/{run_id}/trace
```

The endpoint accepts no query parameters. A browser cannot submit `task_id`, `dispatch_id`, generation, parent span, SHA, branch or token selectors to manufacture a causal chain.

Accepted failure behavior:

- browser-authored selectors → HTTP 400 before projection;
- unknown Run → HTTP 404;
- bounded trace unavailable → HTTP 409;
- contradictory/corrupt source facts → HTTP 500.

The endpoint performs no runtime mutation.

## Bounded projection accepted

The read path is bounded rather than transcript-like:

- runtime event scan cap: 5,000;
- bounded event pages;
- bounded `TaskTraceBatch` spans;
- bounded final `CausalRunTrace` spans;
- metadata-only fields avoid unbounded prompt/result bodies.

Runs that exceed diagnostic projection bounds return an unavailable result instead of weakening limits.

## Acceptance tests

Step 5.6-specific coverage includes:

- `tests/test_trace_collector.py`
  - metadata-only collection and privacy constraints;
- `tests/test_trace_agent_instrumentation.py`
  - production Developer/Reviewer/Repair instrumentation, schema-repair turn and content redaction;
- `tests/test_causal_trace_projector.py`
  - full causal tree and generation mismatch fail-closed behavior;
- `tests/test_trace_worker_sidecar.py`
  - trace persistence failure isolation and generation-context reset;
- `tests/test_trace_persistence.py`
  - real PostgreSQL typed/fenced `TRACE_BATCH` round trip and runtime-event correlation;
- `tests/test_trace_api.py`
  - fixed metadata-only read projection and rejection of browser-authored selectors.

The complete existing suite also proves the optional trace path preserves prior Phase 1–6 execution behavior.

## Implementation-head acceptance evidence

Exact implementation head before documentation/progress/workflow ledger commits:

`e4942113ae5d10283cb31e6be3f832d04a782b61`

Backend Quality run **#919** (`32497790074`) on that head:

- PostgreSQL + Redis: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- no Step 5.6 schema migration required;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **412 passed in 37.91s**.

Frontend Quality run **#212** (`32497790067`) on the same implementation head:

- locked dependency install: **PASS**;
- TypeScript typecheck: **PASS**;
- lint: **PASS**;
- tests: **PASS**;
- production build: **PASS**.

The one Backend warning is an unrelated FastAPI/Starlette TestClient deprecation and is not a Step 5.6 failure.

## Candidate ledger-head acceptance evidence

The complete candidate ledger head containing:

- `docs/CAUSAL_TRACE_CORRELATION.md`;
- this acceptance ledger in pre-final status;
- the Step 5.6 `docs/PROGRESS.md` entry;
- Backend and Frontend workflow path gates;

was:

`f0ca4d5f76cfc376d65cbc342648f01a8faf4939`

Backend Quality run **#924** (`32498551791`) on that head:

- PostgreSQL + Redis: **PASS**;
- Alembic `base → 0007 → base → 0007`: **PASS**;
- verification Docker image: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- deterministic control-plane demos: **5 / 5 PASS**;
- pytest: **412 passed, 1 warning in 37.64s**.

Frontend Quality run **#217** (`32498551783`) on that head: **PASS**.

PR #39 had **0 inline review threads** at the acceptance transition.

## Final accepted-state verification

This file now records the accepted status only after the implementation head and the complete candidate ledger head independently passed both quality workflows.

Because this status transition itself changes an acceptance-ledger path, the resulting accepted-state head must independently pass Backend Quality and Frontend Quality once more. That final green accepted-state head is the terminal acceptance fact; no additional document mutation is required merely to copy its own workflow identifiers back into itself.

PR #39 must remain Draft and unmerged through that final verification.

## Explicitly deferred

Step 5.6 does not complete:

- Step 5.7 — broader Operator Recovery / Approval Surface;
- Step 5.8 — full Chaos / Recovery Benchmark and V1.1 final acceptance.

The Phase 6 Human Gate UI remains accepted for its narrower repair-approval purpose, but it does not imply the complete Step 5.7 operator surface exists.

## Next authority transition

The next question is no longer:

```text
What happened inside this Run generation?
```

Step 5.6 can now answer that diagnostically.

Step 5.7 must answer:

```text
Given durable recovery state plus a causal explanation,
what explicit operator actions are useful,
and how does every mutating action get freshly revalidated
against PostgreSQL, Git, lease and fencing authority?
```

The trace may explain why an action is being considered. It must never authorize that action.
