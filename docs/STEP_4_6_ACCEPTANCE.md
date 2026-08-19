# Step 4.6 Acceptance — Run Metrics

Status: **ACCEPTED / COMPLETE**

Step 4.6 is accepted with the following frozen guarantees and boundaries:

- Run `status` comes only from validated persisted Run truth and is explicitly labeled with `status_basis = PERSISTED_RUN`;
- evidence counters count accepted typed evidence records without translating counts into success, approval, or health verdicts;
- runtime-event counters are derived only from persisted typed events belonging to the requested Run;
- runtime-event metrics require a contiguous Run sequence beginning at 1;
- cross-Run events, sequence gaps, invalid timestamps, or inconsistent aggregates fail closed;
- terminal duration is calculated only from persisted `started_at` and `finished_at`;
- a running Run exposes no invented terminal duration;
- the Metrics DTO contains no success rate, pass rate, approval rate, health score, weighted score, or success threshold;
- Metrics aggregation is bounded to 1,000 events per page and at most 10,000 events per complete projection;
- a Run above the event scan limit returns unavailable rather than presenting partial counters as complete;
- `GET /api/v1/runs/{run_id}/metrics` accepts no browser-authored selectors and exposes no Metrics mutation endpoint;
- no Metrics table, database migration, Metrics evidence write, or scheduler/finalization write path is introduced;
- React renders backend-provided counters read-only and exposes no runtime mutation controls;
- SSE only invalidates/refetches the Metrics query after a validated persisted event and never increments counters locally;
- Step 4.7 GitHub branch/Draft PR publication and Step 4.8 benchmark/demo behavior remain deferred.

## Final exact-head implementation acceptance

Accepted implementation head before ledger advancement:

`1aa6d2554a7f7e8aa441ab4f7823af7e6b92f589`

Backend Quality on that exact head:

- PostgreSQL + Redis service startup: **PASS**;
- existing Alembic `0001 → 0002 → 0003 → 0004 → 0005 → downgrade base → 0001 → 0002 → 0003 → 0004 → 0005`: **PASS**;
- no Step 4.6 database migration was introduced;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- persisted Run status / no-success-score regression: **PASS**;
- typed evidence counter regression: **PASS**;
- persisted terminal timestamp duration regression: **PASS**;
- cross-Run runtime event rejection: **PASS**;
- runtime-event sequence-gap rejection: **PASS**;
- bounded event scan overflow returns unavailable: **PASS**;
- browser selector rejection and GET-only API regression: **PASS**;
- warning-free ASGI/httpx API test transport: **PASS**;
- complete backend `pytest`: **322 passed in 32.23s** with no pytest warning summary.

Frontend Quality on the same exact head:

- locked `npm ci`: **PASS**;
- strict TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **21 passed across 5 test files**;
- descriptive Metrics rendering with no browser success score: **PASS**;
- SSE → backend Metrics query invalidation/refetch regression: **PASS**;
- browser-local counter derivation absent: **PASS**;
- existing DAG/SSE/Diff/Product-page regressions: **PASS**;
- Vite production build: **PASS**.

Merge review confirmed that the Metrics API is read-only, no counter is used to update Run state, no frontend code locally increments authoritative counters, scan overflow cannot be presented as a complete total, and no persistence/scheduler/Git authority was added.

Both `docs/RUN_METRICS.md`, this acceptance document, and `docs/PROGRESS.md` are covered by Backend Quality and Frontend Quality path gates. The acceptance/progress ledger head must therefore pass both workflows again before merge.

Frozen Step 4.6 principle:

> **Typed evidence decides runtime truth; metrics only summarize that accepted truth.**
