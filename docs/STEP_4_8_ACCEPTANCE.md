# Step 4.8 Acceptance — Benchmark / Demo Suite

Status: **ACCEPTED / COMPLETE**

## Accepted scope

Step 4.8 closes the Phase 4 / V1.0 roadmap with a versioned, bounded evaluation layer and a deterministic control-plane demo suite while preserving the existing runtime authority boundary.

Frozen principle:

> **Benchmarks measure accepted runtime truth; they never create or replace runtime truth.**

## Acceptance evidence

The accepted implementation proves all of the following:

- benchmark suites are strict, versioned, bounded, and canonically SHA-256 identified;
- fixture repository URLs are credential-free public GitHub HTTPS URLs;
- the V1 live fixture suite targets stable `benchmark-fixtures/v1-base` at `d141eba7df1d2d5016de2589152d5ab2518778ab`;
- fixture `expected_base_commit` is comparison-only and is never sent as `RunCreateRequest` authority;
- live benchmark execution uses the existing Project/Run Product API and the ordinary Dramatiq/runtime path;
- backend-selected base mismatch becomes `FIXTURE_DRIFT / NOT_EVALUATED` rather than fabricated failure or silent fixture mutation;
- broker unavailability, timeout, and Product API failures remain explicit bounded non-terminal observations;
- terminal observations use persisted Run status, persisted Run Metrics, typed evidence identities, and evidence-bound Task Diff;
- incomplete/truncated/omitted Diff data cannot be mislabeled as a complete code-delta comparison;
- latency uses persisted `terminal_duration_ms`, never browser/client elapsed time;
- benchmark verdicts are read-only comparisons and cannot write Run status, evidence, leases, `run_token`, verification, Reviewer, Repair, Integration/Human Gate, or publication state;
- report hashes remain deterministic for identical typed suite + observation inputs;
- no benchmark database, scheduler, worker, verifier, Reviewer override, privileged demo mode, or runtime-success shortcut exists.

## Development-plan metrics

The final report provides the raw descriptive evaluation metrics required by `docs/DEVELOPMENT_PLAN.md`:

- task success rate;
- first-pass success rate;
- success after repair;
- average retry count;
- Reviewer rejection rate;
- scope violations detected;
- mean terminal latency;
- median terminal latency;
- prompt token usage where durably available;
- completion token usage where durably available;
- estimated model cost only when authoritative data exists.

These aggregates remain measurement only. No health score, weighted score, threshold, or aggregate value is allowed to finalize or rewrite runtime truth.

The rate denominators are explicit: terminal benchmark cases for task/first-pass/repaired success and retry averages; typed Review decisions for Reviewer rejection rate.

## Typed failure/review aggregation

Reviewer rejection and scope violation statistics are not inferred from logs or model prose.

```text
PersistedEvidence
        ↓
hash validation + decode_evidence()
        ↓
ReviewDecision / FailureReport
        ↓
CHANGES_REQUESTED / SCOPE_VIOLATION
        ↓
descriptive counters
```

Corrupted typed evidence fails closed instead of being counted.

## Token/cost availability

Developer and Repair token usage is durably present in their typed evidence and is aggregated after consistency validation.

Current authoritative coverage is therefore:

```text
token_usage = PARTIAL
token_usage_scope = DEVELOPER_REPAIR_ONLY
```

Reviewer token usage is explicitly unavailable because `ReviewDecision` does not durably carry it. Estimated model cost is explicitly `NOT_AVAILABLE`; Step 4.8 does not infer it from text length, token totals, or changing provider prices.

## Required deterministic demos

`benchmarks/v1/control-plane-demo.json` contains exactly five versioned scenarios:

1. **NORMAL_SUCCESS** — Developer changes only `module.py` → protected golden pytest remains read-only → default Docker `DeterministicVerifier` runs `pytest -q` → Reviewer PASS → success without repair;
2. **SCOPE_VIOLATION** — protected test tampering → Git changed paths → typed `SCOPE_VIOLATION`;
3. **REVIEW_REPAIR** — hard checks PASS → Reviewer `CHANGES_REQUESTED` → targeted Repair → re-verification → Reviewer PASS;
4. **INVALID_AGENT_OUTPUT** — invalid Planner structure → bounded schema repair → valid `TaskContract`;
5. **PARALLEL_CONFLICT** — `ParallelWorkerCoordinator` runs two tasks concurrently in isolated worktrees → independent task commits → `TopologicalMergeQueue` → real Git conflict → `GitMergeConflictClassifier` structured conflict evidence.

The strengthened Parallel Conflict demo demonstrates the actual parallel/worktree/merge-queue chain rather than only calling the classifier on preconstructed evidence.

The Normal Success demo deliberately uses the same Docker verification path as production verification instead of relying on an environment-sensitive custom `python -c` string. Its terminal assertion includes typed `FailureReport` details so a future deterministic-gate failure is diagnosable rather than opaque.

## Deterministic vs live separation

```text
devflow-benchmark demo
    = deterministic scripted-provider control-plane proof

devflow-benchmark run
    = stochastic live Product API benchmark

devflow-benchmark evaluate
    = deterministic offline evaluation
```

CI executes `demo` but does not require a paid SiliconFlow request. Live model benchmarking remains opt-in and uses the normal Product API/runtime.

## Experiment identity

Live benchmark execution requires an explicit `OPERATOR_DECLARED` identity recording:

- runtime commit;
- provider;
- Developer model;
- Reviewer model;
- Repair model;
- context strategy;
- verifier identity;
- optional Planner model when applicable.

The deterministic demo separately records the Git commit actually executed.

## Final implementation-head quality evidence

Final hardening implementation head before this acceptance-ledger-only update:

`f07e95fbfbcb6d5179925c41ce573d2cb63cd47d`

Backend Quality:

- PostgreSQL + Redis services: **PASS**;
- Alembic full upgrade → downgrade base → re-upgrade through `0006`: **PASS**;
- verification sandbox image build: **PASS**;
- Ruff: **PASS**;
- V1 fixture validation: **PASS**;
- five deterministic control-plane demos: **5 / 5 PASS**;
- full backend pytest: **361 passed in 29.50s**.

Frontend Quality on the same final hardening head:

- locked dependency install: **PASS**;
- TypeScript strict typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **PASS**;
- Vite production build: **PASS**.

The acceptance sequence also hardened CI governance: the previously ambiguous job name `quality` was replaced with the unique required-check names **Backend Quality** and **Frontend Quality**. An intermediate ledger run correctly exposed an environment-sensitive Normal Success verification command; that demo was replaced with a protected pytest + Docker verifier hard gate. A later full-pytest ledger run exposed a pre-existing lease-lifecycle timing race caused by an 80 ms acquisition window followed by a 50 ms renewed lease. Only the test timing was stabilized so heartbeat is proven while generation 1 is live and takeover is exercised only after the renewed lease deliberately expires; production `renew_task_lease()` still rejects expired generations fail-closed. The resulting exact implementation head above passed the complete quality chain.

PR #33 merge review found no unresolved review threads after the hardening pass.

## Acceptance-ledger gate

This acceptance document and `docs/PROGRESS.md` are themselves Backend + Frontend workflow-triggering paths. This final acceptance-ledger-only commit must therefore pass the uniquely named **Backend Quality** and **Frontend Quality** workflows on its own exact head before PR #33 may leave Draft and merge.

No live SiliconFlow request or live GitHub publication mutation is required for that final ledger gate.

## V1.0 conclusion

Step 4.8 is **ACCEPTED / COMPLETE**. With Steps 4.1–4.8 accepted, Phase 4 — V1.0 Productization is **ACCEPTED / COMPLETE** and DevFlow V1.0 is **ACCEPTED** subject only to the exact-head ledger CI and squash-merge mechanics recorded above.

Final boundary:

> **The benchmark may measure success, repair, failure, latency, and available usage evidence; only the accepted runtime decides what actually happened.**
