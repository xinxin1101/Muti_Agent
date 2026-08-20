# Phase 4 / Step 4.8 — Benchmark / Demo Suite

## Purpose

Step 4.8 makes DevFlow reproducibly evaluable and demoable without adding a second runtime,
verification path, success rule, or privileged demo mode.

Frozen principle:

> **Benchmarks measure accepted runtime truth; they never create or replace runtime truth.**

## Authority chain

```text
versioned BenchmarkSuite
        ↓
external devflow-benchmark client
        ↓
existing Product API
        ↓
existing Project / Run creation
        ↓
existing Dramatiq worker + lease / heartbeat / run_token fencing
        ↓
existing verification / Reviewer / Repair / Integration truth
        ↓
persisted terminal Run
        ↓
existing Metrics / Task Detail / Diff read models
        ↓
bounded BenchmarkObservation
        ↓
deterministic ground-truth comparison
        ↓
multi-dimensional BenchmarkReport
```

The benchmark package has no database engine, persistence store, worker actor, scheduler, Git
mutation primitive, verification override, Reviewer override, Human Gate mutation, GitHub publisher,
or `run_token` capability.

## Versioned fixture identity

A benchmark suite is strict Pydantic data with:

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
default branch
expected_base_commit
TaskContract
ground-truth expectations
tags
```

`expected_base_commit` is **comparison data only**. It is not included in `RunCreateRequest`.
The backend continues to derive `base_commit` from the managed workspace as accepted in Step 4.2.

After Run creation:

```text
backend launch.base_commit == fixture.expected_base_commit
        ↓ yes
continue benchmark observation

backend launch.base_commit != fixture.expected_base_commit
        ↓
FIXTURE_DRIFT
        ↓
NOT_EVALUATED
```

A drifted fixture never silently changes the expected base and never receives a benchmark quality
verdict.

The committed V1 demo suite uses the stable public branch:

```text
benchmark-fixtures/v1-base
```

at:

```text
d141eba7df1d2d5016de2589152d5ab2518778ab
```

This isolates demo fixture identity from later `main` changes.

## Same accepted runtime

The live runner uses only these existing Product API operations:

```text
POST /api/v1/projects
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/metrics
GET  /api/v1/runs/{run_id}/tasks/{task_id}
GET  /api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=TASK
```

There is no `/benchmark/run` runtime shortcut and no special worker queue.

The `/runs` request body remains exactly:

```text
project_id
task
```

The benchmark client never sends:

```text
base_commit
expected_base_commit
run_token
lease owner
verification result
Reviewer decision
terminal Run status
GitHub token
publication branch
```

The runner is sequential by default. Deterministic case order comes from the fixture file, rather
than concurrency timing.

## Typed observations

Live Product facts are converted to a compact `BenchmarkObservation`.

A terminal observation may contain only:

- suite/case identity;
- Project / Run UUID;
- dispatch status;
- backend-selected base commit;
- persisted terminal Run status;
- persisted `terminal_duration_ms`;
- task-level evidence-kind identities;
- descriptive Run Metrics counters;
- bounded accepted Task Diff metadata and changed paths.

It does **not** include:

- prompts;
- raw model responses;
- source context;
- verifier stdout/stderr;
- raw event attributes;
- credentials;
- `run_token`;
- publication claim tokens;
- raw GitHub responses.

Non-terminal benchmark states are explicit:

```text
FIXTURE_DRIFT
DISPATCH_UNAVAILABLE
TIMEOUT
API_ERROR
```

Each carries only a bounded code/message and becomes `NOT_EVALUATED`.

## Ground-truth comparison

Step 4.8 deliberately does not create one quality score.

Each terminal case is compared independently across:

### Completion

```text
persisted Run.status
vs
fixture terminal_status
```

The benchmark reads the terminal status. It cannot set it.

### Evidence

The required typed evidence kinds must be present in Task Detail. The benchmark does not inspect
raw evidence payloads and does not infer Reviewer/verification success from counts.

### Code delta

The fixture declares expected changed repository paths and one mode:

```text
EXACT
SUBSET
```

Comparison uses the existing evidence-bound Task Diff projection from Step 4.5.

If the Product Diff is truncated, contains omitted files, or is unavailable, code-delta comparison
is `NOT_EVALUATED`; bounded output is never mislabeled as complete ground truth.

### Reliability

Optional budgets compare descriptive accepted counters such as:

```text
repair_attempts <= max_repair_attempts
human_decisions <= max_human_decisions
```

These comparisons do not rewrite or reinterpret the Run result.

### Latency

Latency uses only Step 4.6 persisted:

```text
finished_at - started_at
        ↓
terminal_duration_ms
```

The benchmark never uses browser/client wall-clock time to manufacture terminal duration.

### Cost / token usage

The accepted Product Metrics projection does not currently expose an authoritative token/cost
field. Therefore Step 4.8 reports:

```text
cost_data = NOT_AVAILABLE
```

It does not estimate cost from text length, logs, model output, or provider pricing.

## Verdict semantics

A case can be:

```text
MATCHED
MISMATCHED
NOT_EVALUATED
```

This is a **benchmark comparison verdict**, not a DevFlow Run state.

There is no:

```text
benchmark score > threshold
        ↓
Run SUCCEEDED
```

and no benchmark code path imports a persistence mutation interface to do so.

The suite summary exposes counts per comparison dimension and case verdict. It emits no aggregate
success score, health score, or weighted quality score.

## Deterministic report identity

Suite identity is a canonical SHA-256 over normalized typed fixture data.

`BenchmarkReport.report_sha256` is computed over:

```text
suite identity
execution configuration
ordered case evaluations
dimension summary
```

The report contains no generation timestamp. Given the same normalized suite + observation bundle,
offline reevaluation produces the same report SHA-256.

Live Runs are naturally allowed to produce different observed latency, repair counts, or model
outcomes. Reproducibility means those differences are explicit facts under the same versioned
fixture/configuration rather than hidden by an unstable scoring procedure.

## CLI

The installed backend exposes:

```text
devflow-benchmark validate
devflow-benchmark run
devflow-benchmark evaluate
```

### Validate

Schema-validates and hashes one suite. No network or runtime execution occurs.

### Run

Runs each case through the normal Product API, stores bounded observations, then evaluates them.

### Evaluate

Reevaluates a prior observation bundle entirely offline. This path performs no database, Git,
GitHub, model, worker, or runtime mutation.

CLI exit code describes benchmark-command/evaluation success only. It is not fed back into DevFlow
Run finalization.

## Endpoint and credential safety

Fixture repository URLs are restricted to credential-free:

```text
https://github.com/<owner>/<repo>
```

Benchmark API origins:

- may use HTTPS remotely;
- may use plaintext HTTP only on loopback;
- may not contain username/password, query strings, or fragments.

The benchmark CLI has no token option and sends no Authorization header.

`httpx` is promoted to a runtime dependency because Step 4.7 GitHub publication already imports it
in production and Step 4.8 also uses it for the Product API client. This closes the previous
development-extra-only packaging gap without changing publication authority.

## V1 demo cases

The committed suite contains three bounded single-task examples:

1. exact text marker creation;
2. one typed Python `add()` function;
3. one exact JSON contract.

Each has a one-file writable scope and deterministic Python verification command. They exercise the
same Developer → verifier → Reviewer → Repair/runtime path as ordinary Product Runs.

The suite is opt-in for live execution. CI validates the fixture/schema/evaluator/client through
deterministic tests and does **not** call SiliconFlow or perform a live GitHub publication.

## Explicitly absent

- no benchmark database table;
- no benchmark persistence mutation API;
- no benchmark Run status;
- no benchmark-only worker;
- no benchmark-only verifier;
- no Reviewer bypass;
- no Human Gate bypass;
- no lease or `run_token` bypass;
- no privileged demo mode;
- no benchmark-triggered GitHub publication;
- no aggregate success score;
- no inferred token/cost data;
- no paid model call in CI.
