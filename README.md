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
- Release Hardening V2 — real-world clone-to-run readiness — **ACCEPTED / COMPLETE**

See `docs/PROGRESS.md` and the acceptance documents for commit-bound CI evidence.

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

The accepted V2.6 local/runtime matrix is intentionally narrow and reproducible:

- Python **3.11** (`.python-version`)
- Node.js **24** (`.nvmrc`, `frontend/.npmrc` enforces the engine)
- Docker Desktop / Docker Engine with Compose v2
- Git

## 1. Clone and configure

```bash
git clone https://github.com/xinxin1101/Muti_Agent.git
cd Muti_Agent
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit the repository-root `.env` and set at least:

```text
DEVFLOW_DATABASE_URL=postgresql+psycopg://devflow:devflow@127.0.0.1:5432/devflow
DEVFLOW_REDIS_URL=redis://127.0.0.1:6379/0
SILICONFLOW_API_KEY=<your key>
```

Agent model ids are explicit configuration in `.env`. `/readyz` checks the live SiliconFlow catalogue and reports `NOT_READY` if a configured model disappears; DevFlow never silently falls back to another model.

`DEVFLOW_GITHUB_READ_TOKEN` is optional for private-repository reads. `DEVFLOW_GITHUB_PUBLICATION_TOKEN` is optional for accepted Draft PR publication. `scripts/start-dev.ps1` generates `DEVFLOW_SECRETS_ENCRYPTION_KEY` once when needed; it encrypts project registration publication tokens in PostgreSQL's named Docker volume. Keep this key unchanged so those tokens remain usable after restarts. Restart API/worker processes after editing `.env`.

Agent execution budgets are also configurable in `.env`. The default is a 300-second repair budget, a 90-second maximum per repair-model call, and at least two bounded repair attempts. Worker Heartbeat remains only a lease/liveness mechanism; it never removes agent time limits.

## 2. Start PostgreSQL and Redis

The recommended local stack is portable across Bash and PowerShell and persists PostgreSQL durable truth in a named volume:

```bash
docker compose -f compose.dev.yml up -d --wait
```

Stop services without deleting PostgreSQL data:

```bash
docker compose -f compose.dev.yml down
```

Delete local durable development data only when intentional:

```bash
docker compose -f compose.dev.yml down -v
```

## 3. Install the locked backend environment and migrate

```bash
cd backend
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
alembic upgrade head
```

`devflow-api` performs a startup schema preflight. It fails closed with an actionable migration instruction when PostgreSQL is unreachable or not at the accepted Alembic head.

## 4. Build trusted Python and Node verification bases

From the repository root:

```bash
python scripts/build_verification_bases.py
```

This builds:

```text
devflow-verifier:py311
devflow-verifier:node24
```

Dependency preparation is separate from authoritative verification. Runtime verification remains networkless/read-only and does not implicitly pull images.

## 5. Start API, Worker and Frontend

Terminal A:

```bash
cd backend
devflow-api
```

Terminal B:

```bash
cd backend
dramatiq app.workers.tasks
```

Terminal C:

```bash
cd frontend
npm ci
npm run dev
```

Open:

```text
Frontend: http://127.0.0.1:5173
Liveness: http://127.0.0.1:8000/healthz
Readiness: http://127.0.0.1:8000/readyz
```

`/healthz` proves only process liveness. `/readyz` checks database/schema, Redis, trusted verifier image, provider reachability and configured Agent model ids; readiness is operational evidence only and never declares a Run successful.

## Windows / PowerShell helper

The same flow is available through `scripts/dev.ps1`:

```powershell
.\scripts\dev.ps1 check
.\scripts\dev.ps1 infra
.\scripts\dev.ps1 migrate
.\scripts\dev.ps1 verifier
.\scripts\dev.ps1 api
.\scripts\dev.ps1 worker
.\scripts\dev.ps1 frontend
```

Use separate terminals for `api`, `worker` and `frontend`. The Windows CI gate validates the supported Python/Git path, frontend build and PowerShell syntax; Docker sandbox execution remains covered by the Linux Release Readiness gate.

### One-command Windows startup

After copying `.env.example` to `.env` and setting `SILICONFLOW_API_KEY`, start Docker Desktop and run:

```powershell
.\scripts\start-dev.ps1
```

The script prepares the repository-local Python environment and frontend dependencies when needed, starts PostgreSQL and Redis, applies migrations, builds the verifier images when they are absent, then opens dedicated API, worker, and frontend terminals. Use `-SkipVerifier` for quicker restarts once the trusted verifier images have been built; use `-SkipInstall` only when dependencies are already current.

Stop the local API, worker, frontend, PostgreSQL, and Redis while preserving the PostgreSQL volume:

```powershell
.\scripts\stop-dev.ps1
```

To close only the API, worker, and frontend while leaving PostgreSQL and Redis running, use `-KeepInfra`.

### Lightweight local UI/API mode

For local UI and API exploration without provider credentials, Docker Hub verifier-image pulls, GitHub publication, or executing Agent Runs, use:

```powershell
.\scripts\start-local.ps1
```

It starts local PostgreSQL/Redis, runs migrations, and opens the API, worker, and frontend terminals. `/healthz` and the UI are available, but `/readyz` and Agent Runs remain intentionally unavailable until a `SILICONFLOW_API_KEY` is configured and the trusted verifier images are built.

# Product usage

1. Open **Projects** and register the target repository/default branch.
2. Wait until the managed workspace reports ready.
3. Open **New Run**, choose the Project, enter the natural-language requirement and start the Multi-Agent run.
4. Follow the Run Dashboard for DAG state, SSE/runtime evidence, metrics and task/integration diffs.
5. If a merge conflict requires Human Gate review, explicitly authorize bounded repair or abort. Human approval does not bypass scope, verification, Git provenance or fencing.
6. If durable recovery exposes an Operator action, the server revalidates PostgreSQL/DAG/Git/lease/dispatch/fencing facts before mutation.
7. Inspect causal Trace for diagnostics; Trace never authorizes retry/success/merge.
8. After a terminal accepted Run, configure publication credentials, restart `devflow-api`, refresh the Run page, and create the evidence-bound Draft PR.

# GitHub publication

Add when publication is required:

```text
DEVFLOW_GITHUB_PUBLICATION_TOKEN=<token with minimum repository write/PR permissions>
DEVFLOW_GITHUB_PUBLICATION_TIMEOUT_SECONDS=30
```

Alternatively, enter a Fine-grained PAT during project registration. DevFlow encrypts it with the local `DEVFLOW_SECRETS_ENCRYPTION_KEY` before saving it in PostgreSQL; the `.env` file and `devflow-postgres-data` named Docker volume must both be retained for restart-safe publication.

The publication token can publish only the server-selected accepted integration source. It cannot choose a source commit, declare success, bypass verification or override Human/Operator authority. The legacy `DEVFLOW_GITHUB_TOKEN` remains publication-only compatibility.

# Quality and release tests

Backend:

```bash
cd backend
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
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

Release Readiness additionally validates the Compose file, both trusted verification bases, startup smoke, deterministic autonomous E2E and the real distributed V2.5 path:

```text
HTTP → PostgreSQL → Redis → Dramatiq → OpenAI-compatible provider → verifier → Git integration → evidence
```

A separate **Windows Portability** workflow validates the supported Windows Python/Node/Git/PowerShell surface.

# Deliberate backlog after V2.6

The following are not release blockers for the current résumé/demo scope and remain explicit backlog rather than hidden promises:

- broader Python/Node runtime matrices
- general `uv.lock`/arbitrary pyproject verification-environment support
- cursor pagination for large Run/Project histories
- generalized submodule support and very-large-repository optimization

# Architecture references

- `docs/DEVELOPMENT_PLAN.md` — original implementation plan and milestone boundaries
- `docs/PROGRESS.md` — evidence-driven execution ledger
- `docs/RELEASE_HARDENING_V2_ACCEPTANCE.md` — V2 acceptance snapshot
- `docs/AUTONOMOUS_MULTI_AGENT_PRODUCT_LOOP.md` — autonomous product-loop architecture
- `docs/CAUSAL_TRACE_CORRELATION.md` — metadata-only causal trace
- `docs/OPERATOR_RECOVERY_SURFACE.md` — bounded operator recovery authority
- `docs/CHAOS_RECOVERY_BENCHMARK.md` — V1.1 deterministic chaos/recovery matrix
- `docs/V1_1_ROADMAP.md` — accepted V1.1 durable-runtime roadmap
- `docs/ARCHITECTURE.md` — component boundaries
- `docs/RELEASE_READINESS.md` — clone-to-run release hardening and smoke boundary
