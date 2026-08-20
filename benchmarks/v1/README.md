# DevFlow V1 Benchmark / Demo Fixtures

`demo-suite.json` is the versioned V1.0 demo suite for Phase 4 / Step 4.8.

The suite deliberately targets the stable public branch:

```text
repository: https://github.com/xinxin1101/Muti_Agent
branch:     benchmark-fixtures/v1-base
base:       d141eba7df1d2d5016de2589152d5ab2518778ab
```

That branch is a benchmark fixture, not a runtime success authority. The suite also records
`expected_base_commit`, but the live runner **never sends that SHA to the Run API**. DevFlow still
derives the Run base from its managed workspace. The returned `launch.base_commit` is compared with
the fixture only after Run creation. A mismatch is reported as `FIXTURE_DRIFT / NOT_EVALUATED`.

## Validate without models or a running server

From `backend/`:

```bash
devflow-benchmark validate --suite ../benchmarks/v1/demo-suite.json
```

This validates the strict schema and prints the canonical suite SHA-256.

## Run the live demo suite

Start the normal DevFlow API and workers with the same configuration used by ordinary Product Runs,
then execute:

```bash
devflow-benchmark run \
  --suite ../benchmarks/v1/demo-suite.json \
  --api-base-url http://127.0.0.1:8000 \
  --output ../benchmark-results/v1
```

The runner performs only the existing Product API operations:

```text
POST /api/v1/projects
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/metrics
GET  /api/v1/runs/{run_id}/tasks/{task_id}
GET  /api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=TASK
```

It never calls the GitHub publication POST endpoint and never writes persistence directly.

Outputs:

```text
observations.json
report.json
report.md
```

`observations.json` contains bounded typed Product-API facts only. It does not contain prompts,
raw model output, verifier stdout/stderr, credentials, `run_token`, publication claim tokens, or raw
runtime-event payloads.

## Offline deterministic reevaluation

A captured observation bundle can be evaluated again without a worker, model, GitHub mutation, or
database write:

```bash
devflow-benchmark evaluate \
  --suite ../benchmarks/v1/demo-suite.json \
  --observations ../benchmark-results/v1/observations.json \
  --output ../benchmark-results/v1-reevaluated
```

For the same normalized suite, observation bundle, and execution configuration, the report
SHA-256 is deterministic.

## Important boundary

Benchmark comparison reports five separate dimensions:

- completion;
- required typed evidence;
- accepted code delta;
- reliability budgets;
- persisted terminal latency.

No aggregate success/health score is emitted. Token/cost data is reported as `NOT_AVAILABLE`
because the accepted Product Metrics projection does not currently expose an authoritative token or
cost field. The benchmark does not infer those values from model text or logs.

> **Benchmarks measure accepted runtime truth; they never create or replace runtime truth.**
