# Step 4.6 Acceptance — Run Metrics

Status: **CANDIDATE / PENDING CI**

Step 4.6 is ready for acceptance when the exact PR head proves:

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
- Backend Quality and Frontend Quality remain green on the same exact head;
- Step 4.7 GitHub branch/Draft PR publication and Step 4.8 benchmark/demo behavior remain deferred.

Frozen candidate principle:

> **Typed evidence decides runtime truth; metrics only summarize that accepted truth.**
