# Release Readiness Hardening

Status: **IMPLEMENTED / CI VALIDATION PENDING**

This release-readiness increment fixes the public clone-to-run workflow without changing DevFlow's accepted runtime authority model.

Frozen boundary:

> **Release hardening may improve configuration discovery, startup validation, documentation, local networking defaults and smoke coverage; it may not weaken PostgreSQL/Git/lease/fencing/Human/Operator authority or redefine task success.**

## Fixed release blockers

### Repository-root configuration

DevFlow now has one local `.env` contract at repository root. Backend Settings resolves that file by module location rather than process cwd. A relative `DEVFLOW_WORKSPACE_ROOT` is also resolved from repository root, so API and worker processes cannot accidentally create different managed workspace trees merely because they were launched from different directories.

Blank `SILICONFLOW_API_KEY` and `DEVFLOW_GITHUB_TOKEN` values are normalized to `None`; copying `.env.example` does not accidentally enable a provider/publisher with an empty credential.

### Alembic and schema preflight

Alembic now uses the same repository-root Settings source as the Product API. Therefore the documented path:

```text
copy .env.example -> .env
cd backend
alembic upgrade head
```

works without separately exporting `DEVFLOW_DATABASE_URL`.

The Product API performs a read-only startup preflight against `alembic_version`. The accepted revision is `0007_dispatch_attempts`. An unreachable/uninitialized/stale schema aborts startup with an actionable `cd backend && alembic upgrade head` instruction. API startup never runs migrations automatically.

### Current environment/publication semantics

`.env.example` now documents current Lease/generation/`run_token` fencing and durable recovery semantics, removes obsolete Step 3.6 warnings, uses explicit IPv4 loopback defaults, and includes optional GitHub Draft PR publication settings.

### Product documentation

The root README is now the public product entry and includes prerequisites, one `.env`, PostgreSQL/Redis startup, backend installation, migration, verifier build, API/worker/frontend startup, browser product flow, Human/Operator recovery boundaries, GitHub publication and quality commands.

`backend/README.md` retains the Single-task CLI but describes the current production worker semantics: Redis is transport, PostgreSQL is durable truth, Lease establishes liveness ownership, generation/`run_token` fence mutation, reconcilers own recovery, Git validates integration, and Human/Operator surfaces cannot bypass authority.

### Loopback consistency

The Product API binds `127.0.0.1:8000`, the frontend API client defaults to `http://127.0.0.1:8000`, and Vite development/preview servers use explicit IPv4 loopback hosts. CORS retains both `localhost` and `127.0.0.1` compatibility.

## Release smoke

`.github/workflows/release-readiness.yml` is a dedicated startup/product smoke gate. It uses real PostgreSQL 16 and Redis 7 services and deliberately writes only a repository-root `.env`.

The gate proves, in order:

1. Backend and frontend install from their published lock/config files.
2. With an empty PostgreSQL schema, `python -m app.api.main` fails closed and mentions `alembic upgrade head`.
3. `alembic upgrade head` works from `backend/` using only the repository-root `.env`.
4. The schema preflight accepts the migrated PostgreSQL revision.
5. The trusted verifier image builds.
6. `scripts/release_smoke.py` starts the real FastAPI process from `backend/`, a real Dramatiq worker from `backend/`, and the real Vite frontend from `frontend/`. Configuration environment overrides are removed so these processes must recover DB/Redis/token settings through the root `.env`.
7. API `/healthz`, worker process liveness and frontend HTTP readiness are verified.
8. The existing deterministic autonomous product E2E is run against real PostgreSQL/Git to prove requirement -> persisted DAG -> dispatch -> integration -> terminal Run -> Diff -> Draft PR projection without an external model/GitHub dependency.

This smoke complements rather than replaces Backend Quality, Frontend Quality, the 5/5 V1 control-plane demos or the 10/10 V1.1 chaos matrix.

## Target release path

```text
clone
  -> configure one repository-root .env
  -> start PostgreSQL + Redis
  -> alembic upgrade head
  -> build verifier image
  -> start API
  -> start Dramatiq worker
  -> start frontend
  -> create Project
  -> natural-language Run
  -> evidence-bound execution/recovery
  -> Diff / Trace / Metrics
  -> GitHub Draft PR publication
```

## Main promotion strategy

The accepted development history is stacked:

```text
main
  -> PR #38 Phase 6
  -> PR #39 Step 5.6
  -> PR #40 Step 5.7
  -> PR #41 Step 5.8 / V1.1
  -> PR #42 Release Readiness
```

Do not merge only the top stacked PR directly into `main` and leave the lower PRs semantically dangling. After release-readiness acceptance, promote sequentially:

1. merge #38 to `main`;
2. retarget #39 to `main`, require strict CI, merge;
3. retarget #40 to `main`, require strict CI, merge;
4. retarget #41 to `main`, require strict CI, merge;
5. retarget #42 to `main`, require Backend + Frontend + Release Readiness, merge;
6. verify the resulting `main` head again with the same required workflows.

The release remains blocked until Backend Quality, Frontend Quality and Release Readiness are green on the final release-readiness head.
