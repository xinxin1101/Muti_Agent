# Step 4.8 Acceptance — Benchmark / Demo Suite

Status: **CANDIDATE / PENDING CI**

Step 4.8 may be accepted only when the exact PR head proves:

- benchmark fixtures are strict, versioned, bounded, and canonically SHA-256 identified;
- fixture repository URLs are credential-free public GitHub HTTPS URLs;
- the V1 demo suite targets the stable `benchmark-fixtures/v1-base` fixture branch;
- fixture `expected_base_commit` is comparison-only and is never sent to `RunCreateRequest`;
- live execution uses the existing Project / Run Product API rather than a benchmark-only runtime;
- benchmark code contains no persistence mutation store, scheduler, worker, verifier override,
  Reviewer override, Integration/Human Gate mutation, GitHub publisher, or `run_token` capability;
- backend-selected Run base mismatch becomes `FIXTURE_DRIFT / NOT_EVALUATED`;
- broker unavailability, timeout, and bounded Product API failures are explicit non-terminal
  benchmark observations rather than fabricated Run failures;
- terminal benchmark observations use persisted Run status and persisted Run Metrics status
  consistently;
- evidence comparison uses typed evidence-kind identities rather than raw model/evidence payloads;
- code-delta comparison uses the existing evidence-bound Task Diff projection;
- incomplete/truncated/omitted Diff data cannot be labeled a complete code-delta comparison;
- latency uses persisted `terminal_duration_ms`, not client/browser elapsed time;
- reliability compares descriptive repair/Human Gate counters without rewriting runtime truth;
- authoritative token/cost data is not available and is reported as `NOT_AVAILABLE`, never inferred;
- report dimensions remain separate and no aggregate success/health/weighted score is emitted;
- `MATCHED / MISMATCHED / NOT_EVALUATED` remains a benchmark verdict only and has no Run write path;
- suite and report hashes are deterministic for semantically identical typed inputs;
- Product API error messages retained in observations are bounded;
- benchmark API base URLs reject embedded credentials/selectors and restrict plaintext HTTP to
  loopback;
- CLI exposes validate/run/evaluate without accepting provider/GitHub credentials;
- offline evaluate performs no runtime, database, Git, GitHub, worker, or model mutation;
- `httpx` is a production dependency, closing the Step 4.7 packaging gap;
- committed demo fixture validation runs in Backend Quality;
- Backend Quality and Frontend Quality cover Step 4.8 docs/progress on the same exact acceptance
  head;
- no live SiliconFlow call or live GitHub publication is required by CI;
- `docs/PROGRESS.md` remains on Step 4.7 until these gates are green.

Frozen candidate principle:

> **Benchmarks measure accepted runtime truth; they never create or replace runtime truth.**
