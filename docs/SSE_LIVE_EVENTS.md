# Step 4.3 Design — SSE Live Status / Logs

Status: **IMPLEMENTATION CANDIDATE**

Step 4.3 connects the accepted Phase 3 structured runtime event projection to the Step 4.2 Run Dashboard without moving any runtime authority into the browser.

## Authority boundary

```text
accepted runtime fact
        ↓
PostgreSQL runtime_events
        ↓
Run-scoped monotonic sequence
        ↓
read-only FastAPI SSE projection
        ↓
browser EventSource
        ↓
Run Dashboard live timeline
```

The SSE layer is observability only.

It does **not**:

- schedule tasks;
- acquire or renew leases;
- create or validate `run_token`;
- write evidence;
- decide verification success;
- decide Reviewer outcome;
- advance integration state;
- decide terminal Run status.

The existing typed persistence, deterministic verifier, Reviewer, integration gates, Git truth, lease ownership, and `run_token` fencing remain authoritative.

## Endpoint

Step 4.3 adds:

```text
GET /api/v1/runs/{run_id}/events
```

The endpoint accepts an optional non-negative `after_sequence` query cursor. Browser reconnects may also supply the standard `Last-Event-ID` header. The effective cursor is the furthest valid sequence of the two.

Before returning a streaming response, the endpoint performs an initial PostgreSQL read so an unknown Run returns a normal HTTP 404 instead of failing after a 200 stream has already started.

## Resume and ordering semantics

Each persisted runtime event is encoded as:

```text
id: <run-scoped sequence>
data: <one-line JSON PersistedRuntimeEvent>

```

The SSE `id` is the existing monotonic PostgreSQL event sequence. No new event identity or offset system is introduced.

The browser therefore gets native EventSource recovery:

```text
last accepted browser event id = N
        ↓ disconnect
browser reconnects with Last-Event-ID: N
        ↓
backend queries sequence > N
        ↓
timeline resumes
```

Runtime sequence remains an observability cursor only. It never authorizes execution or success.

The server polls `PostgresEvidenceStore.list_runtime_events()` in bounded batches. It does not read worker memory and does not introduce Redis Pub/Sub as a second event source.

## Heartbeat

When no new persisted runtime event exists, the stream periodically emits an SSE comment:

```text
: heartbeat after_sequence=<cursor>
```

Heartbeat comments:

- keep intermediary connections active;
- do not receive an SSE `id`;
- do not consume PostgreSQL Runtime sequence;
- do not become persisted evidence;
- do not appear as Run events in the UI.

## Browser validation

The frontend already has the Step 4.1 `RuntimeEventSummary` wire type. Step 4.3 adds runtime validation before an event enters React state.

The browser rejects:

- malformed JSON;
- unknown event enums;
- non-positive identifiers/sequences;
- cross-Run events;
- sequence reuse with a different `event_id`;
- unexpected sequence gaps;
- nested sensitive keys such as `run_token`, credentials, passwords, tokens, or `*_api_key`.

Malformed or unsafe stream data closes that browser stream instead of being rendered as runtime truth.

The Run status badge is **not** updated by assigning SSE event contents into client state. `EVIDENCE_RECORDED` and `RUN_FINALIZED` events only invalidate the existing REST query so the UI reloads typed persisted Run truth.

## Bounds

- PostgreSQL query batch: 200 events;
- persistence hard maximum remains 1000 events per query;
- browser timeline retains the most recent 500 rendered events;
- single SSE event payload is bounded before emission;
- heartbeat and polling intervals are server-owned constants, not browser-controlled parameters.

## Security

`run_token` is not part of `PersistedRuntimeEvent` and remains excluded from the browser boundary.

Step 4.3 adds another SSE-side sensitive-key validation before serialization so a malformed or manually corrupted event row cannot silently stream credential-like attributes to the browser.

The stream carries no provider credentials, Git credentials, database credentials, Redis credentials, or authorization capability.

## Deliberately deferred

Step 4.3 does not introduce:

- DAG visualization — Step 4.4;
- diff viewer — Step 4.5;
- run metrics — Step 4.6;
- GitHub branch / Draft PR publication — Step 4.7;
- benchmark / demo suite — Step 4.8.

Frozen candidate principle:

> **SSE may make accepted runtime history live; it may not become a second runtime history or a second success authority.**
