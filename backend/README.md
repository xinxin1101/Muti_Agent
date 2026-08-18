# DevFlow Backend

Evidence-driven multi-agent software engineering runtime backend.

## Single-task CLI

After installing the backend and configuring `SILICONFLOW_API_KEY`, build the trusted baseline
verification image and run one validated `TaskContract` against a managed local Git repository:

```bash
cd backend
python -m pip install -e ".[dev]"
docker build -f docker/verification.Dockerfile -t devflow-verifier:py311 .
python -m app.cli run --workspace /path/to/repository --task /path/to/task.json
```

The installed console script is equivalent:

```bash
devflow run --workspace /path/to/repository --task /path/to/task.json
```

The workspace must be the top-level directory of a Git repository with a valid `HEAD`.

## Docker verification sandbox

Production CLI verification is fail-closed and uses `DockerSandboxRunner` by default. DevFlow does
not silently fall back to host execution when Docker, its daemon, cgroup resource control, or the
configured verification image is unavailable.

Each verification command runs with these fixed boundaries:

- repository worktree bind-mounted read-only at `/workspace`;
- container root filesystem read-only;
- network mode `none`;
- all Linux capabilities dropped;
- `no-new-privileges=true`;
- non-root container UID/GID (the current non-root host UID/GID when available, otherwise a fixed
  non-root fallback);
- explicit CPU, memory, PID, `/dev/shm`, and temporary-filesystem limits;
- bounded command timeout, with forced container removal on timeout;
- no implicit image pull (`--pull never`).

The baseline `devflow-verifier:py311` image contains the pinned pytest/Ruff toolchain required by the
existing deterministic checks. Projects with additional verification dependencies should build a
trusted project-specific verification image and configure `DEVFLOW_VERIFICATION_SANDBOX_IMAGE`.
Dependencies must be baked into that image ahead of execution; verification itself remains offline.

`pytest` and `ruff check` keep their typed `TEST_FAILURE` / `LINT_FAILURE` semantics. Additional
project-specific commands are accepted only through a sandboxed command runner. The explicit host
runner exists for focused unit tests and refuses project-specific commands.

Sandbox policy is configurable only within bounded resource ranges through:

```text
DEVFLOW_VERIFICATION_SANDBOX_IMAGE
DEVFLOW_VERIFICATION_SANDBOX_CPUS
DEVFLOW_VERIFICATION_SANDBOX_MEMORY_MB
DEVFLOW_VERIFICATION_SANDBOX_PIDS_LIMIT
DEVFLOW_VERIFICATION_SANDBOX_TMPFS_MB
DEVFLOW_VERIFICATION_SANDBOX_SHM_MB
DEVFLOW_VERIFICATION_SANDBOX_TIMEOUT_SECONDS
```

The command emits one JSON evidence bundle containing:

- terminal task state and state-transition history;
- actual Git changed files;
- Developer execution evidence;
- deterministic verification history including execution backend and sandbox policy facts;
- semantic review decisions;
- targeted repair attempts and terminal failures;
- configured Agent model identifiers;
- measured Developer/Repair token usage and latency.

A model saying "done" never marks the task successful. The terminal `SUCCEEDED` state is reachable
only after deterministic verification and independent semantic review both pass.

See `../docs/DEVELOPMENT_PLAN.md` and `../docs/PROGRESS.md` for the staged roadmap and acceptance
ledger.
