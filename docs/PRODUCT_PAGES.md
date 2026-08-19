# Product Pages and Browser API Boundary

Phase 4 / Step 4.2 turns the accepted frontend foundation into a usable product surface.

## Scope

Step 4.2 adds:

- a bounded FastAPI HTTP product boundary;
- read-only Project / Run catalog queries over accepted PostgreSQL persistence;
- backend-managed Project repository provisioning;
- a New Run endpoint that accepts a validated `TaskContract`;
- backend-derived Git `HEAD` as the Run `base_commit`;
- dispatch through the existing `DramatiqTaskDispatcher`;
- Projects, New Run, Runs, Run Dashboard, and Task Detail pages;
- static TaskContract and evidence metadata inspection.

Step 4.2 deliberately does **not** add:

- SSE or WebSocket streaming;
- live log/event subscriptions;
- DAG visualization;
- diff viewing;
- run metrics;
- GitHub branch/Draft PR publication;
- browser-held Git/provider/database/Redis credentials;
- a second scheduler or alternate worker execution path.

## Product authority boundary

The browser may request operations, but it does not provide runtime truth.

For New Run:

```text
browser TaskContract request
        ↓
FastAPI Pydantic validation
        ↓
managed Project workspace
        ↓
Git HEAD → exact base_commit
        ↓
PostgresEvidenceStore.start_run()
        ↓
DramatiqTaskDispatcher.dispatch()
        ↓
existing leased/fenced worker runtime
```

The browser never sends `base_commit`, `run_token`, lease ownership, verification success, Reviewer approval, or terminal Run status.

## Project provisioning boundary

Repository URLs accepted from the browser must be absolute HTTPS URLs and may not contain embedded credentials.

The backend materializes managed repositories under:

```text
DEVFLOW_WORKSPACE_ROOT/repos/<project_id>
```

Credentials, when required by a deployment, belong to backend Git credential configuration and never to `VITE_*` browser variables.

Provisioning is idempotent for an existing matching managed workspace. A mismatched persisted repository/origin fails closed.

## HTTP surface

Step 4.2 introduces:

```text
GET  /healthz
GET  /api/v1/projects
POST /api/v1/projects
GET  /api/v1/projects/{project_id}

GET  /api/v1/runs
POST /api/v1/runs
GET  /api/v1/runs/{run_id}
GET  /api/v1/runs/{run_id}/tasks/{task_id}
```

Product DTOs are bounded projections. Raw persistence rows, `run_token`, credentials, raw model prompts, and raw evidence payloads are not browser DTOs.

Task Detail exposes TaskContract fields and evidence metadata/hash identity, not raw evidence payloads.

## Dispatch failure semantics

A successful persisted Run followed by a Redis broker rejection returns:

```text
dispatch_status = BROKER_UNAVAILABLE
```

The API does not fabricate `QUEUED`, worker success, or Run success. Step 4.2 makes no exactly-once dispatch claim.

## Frontend pages

- **Projects** registers and lists managed repositories.
- **New Run** submits one validated TaskContract through the product API.
- **Runs** lists persisted Run summaries.
- **Run Dashboard** displays persisted Run status, base commit, and task summaries.
- **Task Detail** displays the persisted TaskContract and bounded evidence metadata.

Single-task launch is intentional in Step 4.2 because the existing queued worker boundary is task-scoped. The browser does not bypass the accepted scheduler by inventing its own multi-task dispatch policy.

## Step 4.3 handoff

Step 4.3 may add SSE consumption over structured runtime events. It must build on this HTTP/product boundary rather than moving event authority into React state.
