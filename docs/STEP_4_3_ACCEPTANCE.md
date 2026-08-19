# Step 4.3 Acceptance — SSE Live Status / Logs

Status: **ACCEPTED / COMPLETE**

Step 4.3 is accepted with the following frozen guarantees and boundaries.

- accepted PostgreSQL `runtime_events` remain the only history source consumed by the browser stream;
- `GET /api/v1/runs/{run_id}/events` is a read-only FastAPI SSE projection and cannot mutate runtime state;
- SSE event `id` is the existing Run-scoped monotonic `sequence`; no second offset or ordering system is introduced;
- reconnect resumes from the furthest valid `Last-Event-ID` / `after_sequence` cursor and queries only `sequence > cursor`;
- an unknown Run is preflighted before stream start and returns HTTP 404 rather than failing after a 200 stream has begun;
- invalid resume cursors, cross-Run batches, non-monotonic batches, sensitive nested attributes, and oversized SSE payloads fail closed;
- heartbeat traffic is emitted only as SSE comments, receives no event id, consumes no runtime sequence, and creates no persisted fact;
- streaming reads stay bounded and reuse the accepted `PostgresEvidenceStore.list_runtime_events()` boundary;
- the browser runtime-validates JSON shape, enum values, identifiers, hashes, Run identity, and sequence order before an event enters React state;
- an exact duplicate of the most recently accepted sequence/event identity is idempotent, while backward movement or sequence reuse with a different `event_id` fails closed;
- the browser opens EventSource only after the existing REST Run query has confirmed that the Run exists;
- browser reconnect behavior remains the native EventSource / `Last-Event-ID` mechanism rather than a competing custom reconnect cursor;
- Run status is never assigned from SSE payloads; accepted evidence/finalization events only invalidate the existing REST query so typed persisted Run truth is re-read;
- EventSource is closed on page unmount;
- the rendered timeline is bounded to the most recent 500 events;
- `run_token`, provider/Git/database/Redis credentials, passwords, tokens, and `*_api_key` attributes remain outside the browser stream;
- Backend Quality and Frontend Quality both gate Step 4.3 code plus its design/acceptance/progress documentation while retaining read-only repository permissions;
- no DAG visualization, diff viewer, run metrics, GitHub publication, or benchmark/demo implementation is introduced.

## Acceptance history

- Initial implementation head `ddb773932d0863cfc066e83556660ec866dff27e` established the PostgreSQL → bounded SSE → EventSource → Run Dashboard path. Frontend Quality passed, while Backend Quality stopped before pytest on import-block formatting in the new SSE regression file; PostgreSQL migrations and the verification Docker image had already passed.
- Mechanical formatting head `cbd75e653b3cd7d27d8b74298479b9527c576aa9` still exposed the exact Ruff import-block mismatch because the previous job log had been truncated. This remained a formatting-only gate failure and did not expose a runtime semantic failure.
- Merge-review hardening heads `5c1e804896ddec08e24bf87b1c94261defe8d907` and `e6f0018f4fd873c5f16a3a750b72f44a4ffa059a` tightened browser ordering: EventSource starts only after REST confirms the Run; an exact duplicate current event is idempotent; backward/reused sequence identities fail closed instead of being silently dropped.
- Exact Ruff formatting head `03e2ef1eb196fda6820383c618890546f6928701` passed PostgreSQL migration round-trip, verification Docker build, Ruff, and the complete backend regression suite with **299 passed in 35.63s**. Frontend Quality also passed locked install, strict TypeScript typecheck, lint, runtime-event/Run Dashboard regressions, and Vite production build.
- Workflow-gate head `84e229e4f76fa5389278419cc869f0f8bac2c7f0` added `docs/SSE_LIVE_EVENTS.md` and `docs/STEP_4_3_ACCEPTANCE.md` to both Backend Quality and Frontend Quality path filters, preserving `contents: read`; both workflows passed again.
- The final acceptance/progress ledger update is intentionally covered by those path gates, so the exact PR head must re-run both Backend Quality and Frontend Quality before merge.

Frozen Step 4.3 principle:

> **SSE may make accepted runtime history live; it may not become a second runtime history or a second success authority.**
