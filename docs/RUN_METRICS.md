# Step 4.6 — Evidence-bound Run Metrics

Step 4.6 makes accepted Run activity measurable without creating a second runtime-success authority.

## Authority model

```text
typed / hash-validated PostgreSQL Run evidence
        +
persisted monotonic runtime events
        ↓
bounded backend aggregation
        ↓
ProductRunMetrics DTO
        ↓
GET-only Metrics API
        ↓
React RunMetrics
        ↑
SSE invalidation / refetch only
```

The Metrics projection answers descriptive questions such as:

- how many evidence records were accepted;
- how many verification, review, repair, failure, dispatch, worker, merge, and Human Gate facts were recorded;
- how many persisted runtime events, warnings, errors, lease acquisitions, takeovers, and releases exist;
- what the latest persisted event sequence is;
- how long a terminal Run lasted according to its persisted start and finish timestamps.

It does **not** decide whether a Run, Task, verification attempt, review, repair, or integration should succeed.

## Persisted Run status remains authoritative

The DTO exposes the already persisted Run status together with an explicit basis:

```text
status = RUNNING | SUCCEEDED | FAILED
status_basis = PERSISTED_RUN
```

Metrics never compute status from counters. In particular:

```text
verification_attempts = 3
```

does not imply verification success, and:

```text
review_decisions = 1
```

does not imply Reviewer approval.

Step 4.6 deliberately exposes no `success_rate`, `pass_rate`, `approval_rate`, health score, weighted score, threshold, or browser-configurable success formula.

## Evidence counters

Evidence counters are derived from the already hash-validated `PersistedRunSnapshot.evidence` projection. They count accepted typed evidence kinds; they do not reinterpret payload semantics into a verdict.

Current counters include:

- total evidence records;
- Developer runs;
- verification attempts;
- review decisions;
- repair attempts;
- failure reports;
- dispatch events;
- worker executions;
- merge queue snapshots;
- merge conflicts;
- integration gate evaluations;
- human decisions.

The Metrics layer never reconstructs typed evidence from runtime-event attributes and never writes a new Metrics evidence record.

## Runtime-event counters

Runtime-event metrics are derived only from persisted, typed runtime events. The backend requires:

```text
same run_id
        +
sequence 1, 2, 3, ... N with no gap
```

A cross-Run event, sequence gap, or inconsistent aggregate fails closed as persistence corruption instead of returning an apparently valid summary.

Current observability counters include:

- total events;
- warning events;
- error events;
- lease acquisitions;
- lease takeovers;
- lease releases;
- latest event sequence.

These counters remain observability only. For example, `error_events = 0` does not make the Run successful.

## Bounded aggregation

Step 4.6 reuses the accepted bounded runtime-event query path rather than introducing an unbounded browser payload or a second event store.

Current bounds are:

- at most 1,000 persisted events per backend page;
- at most 10,000 events in one complete Run Metrics projection.

If a Run has more than 10,000 persisted runtime events, the backend performs a one-record overflow probe and returns Metrics unavailable rather than silently presenting a partial summary.

```text
10,000 events loaded
        ↓
probe sequence > cursor
        ↓
more events exist
        ↓
409 Metrics unavailable
```

A bounded partial total is never labeled as the complete Run total.

## Duration semantics

`terminal_duration_ms` is calculated only when both persisted timestamps exist:

```text
finished_at - started_at
```

For a running Run:

```text
terminal_duration_ms = null
```

The browser does not use its local clock to invent elapsed runtime, and the backend does not convert "time observed so far" into terminal duration.

A persisted `finished_at` earlier than `started_at` fails closed as corruption.

## Product API

```text
GET /api/v1/runs/{run_id}/metrics
```

The endpoint is read-only and accepts no query selectors. Requests such as:

```text
?success_rate=0.9
?threshold=...
?score_weight=...
```

are rejected instead of influencing the projection. There is no POST/PATCH/PUT Metrics mutation surface.

## React boundary

The Run Dashboard renders the typed backend projection and exposes no controls that mutate Run state, evidence, leases, Git state, verification state, Reviewer decisions, or integration state.

SSE is a freshness signal only:

```text
validated persisted runtime event
        ↓
invalidate ["run-metrics", run_id]
        ↓
GET Metrics again
```

The browser never increments counters locally from an SSE message. This prevents duplicate/reordered/reconnected event delivery from becoming a second Metrics truth source.

## Persistence boundary

Step 4.6 introduces:

- no Metrics table;
- no database migration;
- no Metrics event/evidence write;
- no scheduler or Run-finalization write path.

Typed persistence remains runtime truth; structured runtime events remain observability truth; Metrics is only a bounded read projection over those accepted facts.

## Frozen Step 4.6 principle

> **Typed evidence decides runtime truth; metrics only summarize that accepted truth.**

Step 4.7 GitHub branch / Draft PR publication and Step 4.8 benchmark/demo behavior remain deliberately deferred.
