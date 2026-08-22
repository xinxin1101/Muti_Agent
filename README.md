# Muti_Agent / DevFlow

Evidence-driven Multi-Agent software engineering runtime.

DevFlow turns a Git repository plus a natural-language requirement into a validated multi-task execution plan, runs isolated coding agents, verifies their output deterministically, integrates accepted Git commits in dependency order, pauses for durable Human decisions when required, exposes trace/operator recovery as non-authoritative diagnostics/control requests, and can publish the accepted integration result as a GitHub Draft Pull Request.

## Current status

- Phase 1 / V0.1 — Single Task Evidence Loop — **ACCEPTED / COMPLETE**
- Phase 2 / V0.2 — True Multi-Agent Runtime — **ACCEPTED / COMPLETE**
- Phase 3 / V0.3 — Safety, Context and Reliability — **ACCEPTED / COMPLETE**
- Phase 4 / V1.0 — Productization — **ACCEPTED / COMPLETE**
- Phase 5 / V1.1 — Durable Agent Runtime — **ACCEPTED / COMPLETE**
- Phase 6 — Autonomous Multi-Agent Product Loop — **ACCEPTED / COMPLETE**
- Release Hardening V2 — real-world clone-to-run readiness — **IN PROGRESS / NOT ACCEPTED**

See `docs/PROGRESS.md` and the step acceptance documents for commit-bound CI evidence.

## Core principle

> **Agents propose; evidence decides.**

LLM output may propose planning, implementation, review and bounded repair actions. It cannot establish task completion by self-report. Runtime truth comes from validated contracts, PostgreSQL typed evidence, Git parent/provenance checks, scope enforcement, deterministic verification, independent review, durable lease/generation/`run_token` fencing, accepted integration history and explicit Human authorization where policy requires it.

## Product path

```text
repository + natural-language requirement
        ↓
Planner proposal
        ↓
validated + persisted TaskDAG
        ↓
durable root dispatch
        ↓
parallel generation-bound workers
        ↓
deterministic verification / independent review / targeted repair
        ↓
topological Git integration
        ↓
durable downstream reconciliation
        ↓
Human Gate / bounded integration repair when required
        ↓
evidence-bound terminal Run
        ↓
DAG / SSE / Diff / Metrics / Trace / Operator recovery surface
        ↓
GitHub Draft PR publication
```

Trace, UI, Operator actions, readiness checks and benchmark output remain projections/requests. They never replace PostgreSQL, Git, lease/fencing, verification or Human-decision authority.

# Quick Start

## Prerequisites

Install:

- Python 3.11+
- Node.js 22.12+
- Docker
- Git

The local product stack also requires PostgreSQL and Redis. The commands below use Docker containers for both.

## 1. Clone and configure

```bash
git clone https://github.com/xinxin1101/Muti_Agent.git
cd Muti_Agent
cp .env.example .env
```

PowerShell equivalent:

```powershell
Copy-Item .env.example .env
```

Edit the repository-root `.env` and set at least:

```text
DEVFLOW_DATABASE_URL=postgresql+psycopg://devflow:devflow@127.0.0.1:5432/devflow
DEVFLOW_REDIS_URL=redis://127.0.0.1:6379/0
SILICONFLOW_API_KEY=<your key>
```

Agent model ids are explicit configuration in `.env`. DevFlow checks the live SiliconFlow model catalogue through `/readyz`; if a configured model disappears, the product becomes `NOT_READY` instead of silently falling back to another model.

`DEVFLOW_GITHUB_READ_TOKEN` is optional and is used only for private-repository reads. `DEVFLOW_GITHUB_PUBLICATION_TOKEN` is optional and is used only for accepted Draft PR publication. Blank provider/GitHub tokens are treated as **not configured**.

Backend settings and secrets are frozen for the lifetime of each API/worker process. After editing `.env`, restart the affected process; DevFlow intentionally does not hot-reload credentials.

The backend resolves this single repository-root `.env` independently of whether commands are started from the repository root or from `backend/`.

## 2. Start PostgreSQL and Redis

If you do not already run compatible local services:

```bash
docker run --name devflow-postgres \
  -e POSTGRES_USER=devflow \
  -e POSTGRES_PASSWORD=devflow \
  -e POSTGRES_DB=devflow \
  -p 5432:5432 \
  -d postgres:16-alpine

docker run --name devflow-redis -p 6379:6379 -d redis:7-alpine
```

## 3. Install backend and migrate the database

```bash
cd backend
python -m pip install -e ".[dev]"
alembic upgrade head
```

`devflow-api` performs a startup schema preflight. If PostgreSQL is unreachable, the Alembic table is missing, or the database is not at the accepted head revision, startup fails with an actionable `alembic upgrade head` instruction. DevFlow does **not** silently mutate database schema at API startup.

## 4. Build the trusted verification image

From `backend/`:

```bash
docker build -f docker/verification.Dockerfile -t devflow-verifier:py311 .
```

Runtime verification is fail-closed and does not pull verification images implicitly.

## 5. Start the API

Terminal A, from `backend/`:

```bash
devflow-api
```

Liveness check:

```text
http://127.0.0.1:8000/healthz
```

Operational product readiness:

```text
http://127.0.0.1:8000/readyz
```

`/healthz` only proves the API process is alive. `/readyz` checks the current database schema, Redis reachability, configured verifier image, SiliconFlow reachability, and all configured Agent model ids. Readiness remains operational evidence only and never declares a Run successful.

## 6. Start the worker

Terminal B, from `backend/`:

```bash
dramatiq app.workers.tasks
```

Redis/Dramatiq is transport only. The worker reloads durable Run/Task state from PostgreSQL, acquires a generation-bound lease, receives a server-issued `run_token`, and is fenced from accepted mutation after ownership becomes stale.

## 7. Start the frontend

Terminal C:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173
```

The frontend defaults to `http://127.0.0.1:8000` for the API. Override with `VITE_API_BASE_URL` only when intentionally using a different API origin. Port 5173 is strict: if occupied, Vite fails instead of silently moving to an origin not accepted by the API CORS policy.

# Product usage

1. Open **Projects** and register the target Git repository/default branch.
2. Wait until the managed workspace reports ready.
3. Open **New Run**, choose the Project, enter the natural-language requirement and start the Multi-Agent run.
4. Follow the Run Dashboard for DAG state, SSE/runtime evidence, metrics and task/integration diffs.
5. If a merge conflict requires Human Gate review, explicitly authorize bounded repair or abort. Human approval does not bypass scope, verification, Git provenance or fencing.
6. If durable recovery exposes an Operator action, the UI renders only the server-advertised opaque action. The server revalidates PostgreSQL/DAG/Git/lease/dispatch/fencing facts before any mutation.
7. Inspect causal Trace for diagnostics; Trace never authorizes retry/success/merge.
8. After a terminal accepted Run, configure the GitHub publication credential, restart `devflow-api`, refresh the Run page, and create the evidence-bound Draft PR.

# GitHub publication

Add to the repository-root `.env` when publication is required:

```text
DEVFLOW_GITHUB_PUBLICATION_TOKEN=<token with minimum repository write/PR permissions>
DEVFLOW_GITHUB_PUBLICATION_TIMEOUT_SECONDS=30
```

Then restart `devflow-api`. Settings are process-scoped by design, so editing `.env` does not change credentials in an already running API process.

The publication token can publish only the server-selected accepted integration source. It cannot choose a source commit, declare a Run successful, bypass deterministic verification or override Human/Operator authority. The legacy `DEVFLOW_GITHUB_TOKEN` remains a publication-only compatibility alias; it never grants private-repository read capability.

# Quality and release tests

Backend:

```bash
cd backend
ruff check .
devflow-benchmark validate --suite ../benchmarks/v1/demo-suite.json
devflow-benchmark demo --manifest ../benchmarks/v1/control-plane-demo.json --repository-root . --timeout-seconds 180
devflow-benchmark chaos --manifest ../benchmarks/v1_1/chaos-recovery.json --repository-root . --timeout-seconds 300
pytest
```

Frontend:

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm run test
npm run build
```

Release readiness additionally runs the startup/product smoke documented in `docs/RELEASE_READINESS.md`.

# Architecture references

- `docs/DEVELOPMENT_PLAN.md` — original implementation plan and milestone boundaries
- `docs/PROGRESS.md` — evidence-driven execution ledger
- `docs/AUTONOMOUS_MULTI_AGENT_PRODUCT_LOOP.md` — autonomous product-loop architecture
- `docs/CAUSAL_TRACE_CORRELATION.md` — metadata-only causal trace
- `docs/OPERATOR_RECOVERY_SURFACE.md` — bounded operator recovery authority
- `docs/CHAOS_RECOVERY_BENCHMARK.md` — V1.1 deterministic chaos/recovery matrix
- `docs/V1_1_ROADMAP.md` — accepted V1.1 durable-runtime roadmap
- `docs/ARCHITECTURE.md` — component boundaries
- `docs/RELEASE_READINESS.md` — clone-to-run release hardening and smoke boundary
