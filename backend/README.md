# DevFlow Backend

Evidence-driven multi-agent software engineering runtime backend.

The repository-root `../.env` is the single local configuration file. Backend settings resolve it independently of the current working directory, so commands may be started from `backend/` without copying credentials into a second file.

## Product API prerequisites

Install the backend:

```bash
cd backend
python -m pip install -e ".[dev]"
```

Configure PostgreSQL and Redis in `../.env`, then apply the accepted Alembic schema:

```bash
alembic upgrade head
```

The Product API performs a read-only schema preflight at startup. It refuses to start when PostgreSQL is unreachable, the Alembic table is missing, or the revision is not the accepted head. It does not run migrations implicitly.

Build the trusted baseline verification image before executing Runs:

```bash
docker build -f docker/verification.Dockerfile -t devflow-verifier:py311 .
```

Start the API:

```bash
devflow-api
```

The local API binds to `127.0.0.1:8000` and exposes `/healthz`.

## Redis / Dramatiq production worker

Start a worker from `backend/`:

```bash
dramatiq app.workers.tasks
```

The Redis message contains only:

```text
dispatch_id
run_id
task_id
```

Redis/Dramatiq is **transport, not truth**. Queue delivery is permission to attempt work, never execution ownership or success authority. The worker reloads the persisted Run/Task/DAG facts and managed workspace before execution.

Current accepted ownership/recovery semantics are:

```text
PostgreSQL Run / Task / DAG / dispatch / evidence
        ↓ durable truth
Lease acquisition
        ↓ current liveness owner
generation + server-issued run_token
        ↓ stale-write fencing
isolated generation-bound Git worktree
        ↓
Developer / deterministic verification / Reviewer / bounded Repair
        ↓
WorkerExecutionEvidence
        ↓ terminal worker fact
DAG / task reconciler
        ↓ safe recovery / redispatch
Git parent validation + CAS
        ↓ integration authority
Human Gate / Operator action
        ↓ request / authorization only
fresh authoritative revalidation
```

Dramatiq's task actor intentionally disables automatic framework retries (`max_retries=0`). Recovery is not delegated to broker redelivery. Durable recovery is performed through PostgreSQL dispatch/lease/evidence facts plus the accepted reconciler/controller paths. A stale generation cannot regain authority from Redis delivery, Trace metadata, UI state or an Operator request.

## Docker verification sandbox

Production verification is fail-closed and uses `DockerSandboxRunner` by default. DevFlow does not silently fall back to host execution when Docker, its daemon, cgroup resource control, or the configured verification image is unavailable.

Each verification command runs with these fixed boundaries:

- repository worktree bind-mounted read-only at `/workspace`;
- container root filesystem read-only;
- network mode `none`;
- all Linux capabilities dropped;
- `no-new-privileges=true`;
- non-root container UID/GID;
- explicit CPU, memory, PID, `/dev/shm`, and temporary-filesystem limits;
- bounded command timeout with forced container removal;
- no implicit image pull (`--pull never`).

The baseline `devflow-verifier:py311` image contains the pinned pytest/Ruff toolchain required by existing deterministic checks. Projects with additional verification dependencies should build a trusted project-specific image and configure `DEVFLOW_VERIFICATION_SANDBOX_IMAGE`; verification itself remains offline.

Sandbox policy is bounded through:

```text
DEVFLOW_VERIFICATION_SANDBOX_IMAGE
DEVFLOW_VERIFICATION_SANDBOX_CPUS
DEVFLOW_VERIFICATION_SANDBOX_MEMORY_MB
DEVFLOW_VERIFICATION_SANDBOX_PIDS_LIMIT
DEVFLOW_VERIFICATION_SANDBOX_TMPFS_MB
DEVFLOW_VERIFICATION_SANDBOX_SHM_MB
DEVFLOW_VERIFICATION_SANDBOX_TIMEOUT_SECONDS
```

A model saying `done` never marks a task successful. Terminal success remains evidence-bound to scope/Git checks, deterministic verification and independent review, followed by accepted integration for multi-task Runs.

## Single-task CLI

The original validated `TaskContract` CLI remains available:

```bash
python -m app.cli run --workspace /path/to/repository --task /path/to/task.json
```

or:

```bash
devflow run --workspace /path/to/repository --task /path/to/task.json
```

The workspace must be the top-level directory of a Git repository with a valid `HEAD`.

## Benchmark / recovery checks

```bash
devflow-benchmark validate --suite ../benchmarks/v1/demo-suite.json
devflow-benchmark demo --manifest ../benchmarks/v1/control-plane-demo.json --repository-root . --timeout-seconds 180
devflow-benchmark chaos --manifest ../benchmarks/v1_1/chaos-recovery.json --repository-root . --timeout-seconds 300
```

See `../README.md`, `../docs/PROGRESS.md`, `../docs/V1_1_ROADMAP.md`, and `../docs/RELEASE_READINESS.md` for the public product flow and accepted authority boundaries.
