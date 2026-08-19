# Frontend Foundation

Phase 4 / Step 4.1 establishes the browser-side project and quality baseline for DevFlow.

## Scope

Step 4.1 includes:

- React + TypeScript + Vite application scaffold;
- React Router navigation boundary;
- TanStack Query server-state provider;
- Tailwind CSS build integration;
- a typed HTTP client boundary;
- browser-safe runtime event DTOs aligned with the accepted backend event model;
- Vitest + Testing Library regression baseline;
- a committed npm lockfile for reproducible dependency resolution;
- a dedicated `Frontend Quality` GitHub Actions workflow;
- a minimal application shell with explicit placeholders for later product pages.

Step 4.1 deliberately does **not** implement:

- Project creation or repository management;
- New Run forms or run submission;
- Run Dashboard or Task Detail behavior;
- SSE/WebSocket streaming;
- DAG visualization;
- diff viewing;
- metrics;
- GitHub branch or Draft PR publication;
- any change to accepted scheduler, lease, fencing, persistence, verification, or event semantics.

## Authority boundary

Frontend state is presentation/client state only.

The browser must never become an authority for:

- task scheduling;
- lease ownership or liveness;
- `run_token` write authority;
- verification pass/fail decisions;
- Reviewer decisions;
- integration state;
- run success.

Structured runtime events are observable history. They are not a second source of truth.

## Secret boundary

Only public browser configuration may use Vite's `VITE_*` environment variables.

The browser must never receive:

- model/provider API keys;
- GitHub credentials;
- PostgreSQL credentials;
- Redis credentials;
- `run_token`;
- other worker fencing capabilities.

`frontend/.env.example` therefore contains only `VITE_API_BASE_URL`.

## Frontend layout

```text
frontend/
├── src/
│   ├── api/          typed HTTP boundary
│   ├── app/          application routing and query client
│   ├── components/   shared shell components
│   ├── pages/        bounded route surfaces
│   ├── test/         test setup
│   ├── types/        browser-safe DTOs
│   ├── main.tsx
│   └── styles.css
├── index.html
├── package.json
├── package-lock.json
├── tsconfig*.json
├── vite.config.ts
└── vitest.config.ts
```

## Quality commands

From `frontend/`:

```bash
npm ci
npm run typecheck
npm run lint
npm run test
npm run build
```

CI runs the same locked install and quality gates under Node.js 24.

## Server-state boundary

TanStack Query is installed as the server-state/cache boundary. It does not replace backend truth.

The initial defaults intentionally keep behavior conservative:

- one query retry;
- no mutation retry;
- no automatic refetch on window focus;
- short stale-time suitable for later API consumption.

SSE is intentionally deferred to the dedicated live-status step instead of being smuggled into this scaffold.

## Event DTO boundary

The browser-facing runtime-event model mirrors the accepted backend `PersistedRuntimeEvent` field names and enum values at the wire boundary. It intentionally excludes `run_token` and other credentials. Any later view-model conversion is a presentation concern and must not redefine backend event semantics.

## Step 4.2 handoff

The next product step may replace the Projects/Runs placeholders with real pages and API contracts while preserving all boundaries above.
