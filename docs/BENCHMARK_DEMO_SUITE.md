# Phase 4 / Step 4.8 — Benchmark / Demo Suite

## Purpose

Step 4.8 makes DevFlow reproducibly evaluable and demoable without adding a second runtime, success rule, verifier, scheduler, or privileged demo path.

Frozen principle:

> **Benchmarks measure accepted runtime truth; they never create or replace runtime truth.**

## Two deliberately separate execution modes

### Deterministic control-plane demo

```text
versioned control-plane manifest
        ↓
devflow-benchmark demo
        ↓
scripted/fake provider responses
        +
real DevFlow control-plane components
        ↓
deterministic pytest scenarios
```

This mode is CI-safe and reproducible. It exercises the real control-plane components rather than a benchmark-only runtime.

The required V1 scenarios are exactly:

```text
NORMAL_SUCCESS
SCOPE_VIOLATION
REVIEW_REPAIR
INVALID_AGENT_OUTPUT
PARALLEL_CONFLICT
```

The strengthened parallel-conflict scenario proves the full chain:

```text
ParallelWorkerCoordinator
        ↓
2 concurrent tasks
        ↓
isolated task worktrees
        ↓
independent task commits
        ↓
TopologicalMergeQueue
        ↓
real Git merge conflict
        ↓
GitMergeConflictClassifier
        ↓
structured conflicting path/type/stage evidence
```

The classifier observes and structures the conflict; it does not silently repair it or corrupt the base workspace.

### Live stochastic benchmark

```text
versioned BenchmarkSuite
        ↓
devflow-benchmark run
        ↓
existing Product API
        ↓
existing Project / Run creation
        ↓
Dramatiq worker + lease / heartbeat / run_token fencing
        ↓
existing verification / Reviewer / Repair / Integration truth
        ↓
persisted terminal Run
        ↓
Metrics / Task Detail / Diff read models
        ↓
bounded BenchmarkObservation
        ↓
deterministic offline evaluation
```

A live run may use SiliconFlow and therefore is intentionally separated from deterministic CI. CI does not require a paid SiliconFlow request.

## Versioned fixture identity

A benchmark suite is strict Pydantic data containing:

```text
schema_version
suite_id
suite_version
description
cases[]
```

Each case freezes:

```text
case_id
description
credential-free public GitHub repository URL
default_branch
expected_base_commit
TaskContract
ground-truth expectations
tags
```

The committed V1 fixture suite targets the stable branch:

```text
benchmark-fixtures/v1-base
```

at base:

```text
d141eba7df1d2d5016de2589152d5ab2518778ab
```

`expected_base_commit` is comparison data only. It is never sent as `RunCreateRequest` authority. The backend still derives the real Run base from its managed Git workspace.

A mismatch becomes:

```text
FIXTURE_DRIFT
        ↓
NOT_EVALUATED
```

rather than silently rewriting the fixture or runtime base.

## Product API boundary

Live benchmark execution reuses only existing Product API reads/writes:

```text
POST /api/v1/projects
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/metrics
GET  /api/v1/runs/{run_id}/tasks/{task_id}
GET  /api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=TASK
```

The `/runs` request remains only:

```text
project_id
task
```

Benchmark code cannot author:

```text
base_commit
expected_base_commit
Run terminal status
verification result
Reviewer decision
lease owner
run_token
GitHub publication state
```

There is no `/benchmark/run` shortcut, benchmark database table, benchmark worker, benchmark scheduler, benchmark verifier, or benchmark-only success transition.

## Typed observation boundary

Terminal observations are bounded projections of accepted Product facts:

- suite/case identity;
- Project / Run identity;
- dispatch status;
- backend-selected base commit;
- persisted terminal Run status;
- persisted `terminal_duration_ms`;
- typed evidence-kind identities;
- descriptive Run Metrics counters;
- bounded evidence-bound Task Diff metadata.

They deliberately exclude prompts, raw model responses, source context, verifier stdout/stderr, raw event attributes, credentials, `run_token`, raw GitHub responses, and unbounded evidence payloads.

Non-terminal benchmark states are explicit:

```text
FIXTURE_DRIFT
DISPATCH_UNAVAILABLE
TIMEOUT
API_ERROR
```

Those states become `NOT_EVALUATED`; transport failure is never promoted to a fabricated runtime failure.

## Development-plan evaluation metrics

Step 4.8 records the raw descriptive metrics required by `docs/DEVELOPMENT_PLAN.md`:

```text
task success rate
first-pass success rate
success after repair
average retry count
Reviewer rejection rate
scope violations detected
mean terminal latency
median terminal latency
prompt token usage
completion token usage
estimated model cost when available
```

These values are **evaluation outputs only**. They do not participate in Run finalization, scheduling, verification, Reviewer decisions, repair authorization, Integration/Human Gate decisions, or GitHub publication.

### Denominators

- task success rate = successful terminal cases / terminal benchmark cases;
- first-pass success rate = successful terminal cases with zero repair attempts / terminal benchmark cases;
- repaired success rate = successful terminal cases with one or more repairs / terminal benchmark cases;
- average retry count = total repair attempts / terminal benchmark cases;
- Reviewer rejection rate = typed `CHANGES_REQUESTED` decisions / typed Review decisions.

Scope violations are reported as a typed detected count rather than a made-up rate.

### Source of Reviewer rejection and scope violation facts

Benchmark aggregation does not parse logs or model prose.

```text
PersistedEvidence
        ↓
hash validation
        ↓
decode_evidence()
        ↓
ReviewDecision / FailureReport
        ↓
reviewer_rejections / scope_violations
```

`ReviewDecision.CHANGES_REQUESTED` increments Reviewer rejection count. `FailureReport.failure_type == SCOPE_VIOLATION` increments scope-violation count.

## Token and cost availability

Token accounting is authoritative only where durable typed evidence actually contains usage.

Current V1 evidence supports:

```text
DeveloperRunResult.agent_usage
RepairRunResult.agent_usage
        ↓
prompt_tokens
completion_tokens
total_tokens
```

The Metrics layer verifies:

```text
prompt_tokens + completion_tokens == total_tokens
```

and fails closed on contradictory persisted usage.

Current reporting therefore states:

```text
token_usage = PARTIAL
token_usage_scope = DEVELOPER_REPAIR_ONLY
reviewer_token_usage_available = false
estimated_cost_available = false
cost_data = NOT_AVAILABLE
```

Reviewer token usage is not inferred because `ReviewDecision` does not currently carry durable token accounting. Model cost is not guessed from token count or current provider pricing.

## Experiment identity

A live benchmark invocation must explicitly record an operator-declared execution identity:

```text
runtime_commit
provider
planner_model       optional when Planner is outside Product Run creation
developer_model
reviewer_model
repair_model
context_strategy
verifier_identity
```

The identity basis is:

```text
OPERATOR_DECLARED
```

This records the experiment configuration without pretending the benchmark remotely attested it.

`devflow-benchmark demo` separately records the Git runtime commit it actually executes.

## Comparison dimensions

Each case is evaluated independently across:

1. completion — persisted Run status vs expected terminal status;
2. evidence — required typed evidence kinds;
3. code delta — existing evidence-bound Task Diff projection;
4. reliability — configured repair/Human Gate budgets;
5. latency — persisted terminal duration.

A case verdict is one of:

```text
MATCHED
MISMATCHED
NOT_EVALUATED
```

That verdict is a read-only benchmark comparison. It is not a DevFlow Run state.

The report also emits descriptive aggregate rates required by the evaluation plan, but it emits no health score, weighted score, or threshold that can become runtime authority.

## Deterministic identity and offline reevaluation

Suite identity is a canonical SHA-256 over normalized typed fixture data.

`BenchmarkReport.report_sha256` covers:

```text
suite identity
execution configuration
ordered case evaluations
descriptive summary/aggregates
```

The report has no generation timestamp. Re-evaluating the same typed suite and observation bundle produces the same report identity.

## CLI

```text
devflow-benchmark validate
devflow-benchmark demo
devflow-benchmark run
devflow-benchmark evaluate
```

- `validate`: schema-validates/hashes the suite only.
- `demo`: runs the five versioned deterministic control-plane scenarios through pytest.
- `run`: executes live stochastic cases through the normal Product API and requires experiment identity.
- `evaluate`: performs deterministic offline reevaluation with no runtime mutation.

CLI exit status is benchmark-command/evaluation status only and is never fed back to Run finalization.

## URL and credential safety

Fixture repositories are restricted to credential-free public GitHub HTTPS URLs. Benchmark API origins may be remote HTTPS or loopback HTTP; embedded credentials, selectors, and fragments are rejected.

The benchmark package has no provider/GitHub token CLI option and does not receive `run_token`.

## CI acceptance path

Backend Quality executes:

```text
Alembic upgrade/downgrade/re-upgrade
        ↓
verification sandbox image build
        ↓
Ruff
        ↓
V1 benchmark fixture validation
        ↓
5 deterministic control-plane demos
        ↓
full pytest
```

Frontend Quality independently runs locked install, strict typecheck, lint, Vitest, and production build. Step 4.8 design/acceptance/progress files and benchmark fixtures are workflow-triggering paths for both workflows.

No live SiliconFlow call or live GitHub publication is required by CI.

## Explicitly absent

- no benchmark runtime truth store;
- no benchmark-only scheduler/worker/verifier;
- no verification or Reviewer bypass;
- no Human Gate or `run_token` bypass;
- no benchmark-triggered GitHub publication;
- no health/weighted score used as a runtime gate;
- no inferred Reviewer token usage;
- no inferred model cost;
- no paid model call in CI.

Frozen Step 4.8 principle:

> **The benchmark may measure success, repair, failure, latency, and available usage evidence; only the accepted runtime decides what actually happened.**
