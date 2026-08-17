# DevFlow Backend

Evidence-driven multi-agent software engineering runtime backend.

## V0.1 single-task CLI

After installing the backend and configuring `SILICONFLOW_API_KEY`, run one validated
`TaskContract` against a managed local Git repository:

```bash
cd backend
python -m pip install -e ".[dev]"
python -m app.cli run --workspace /path/to/repository --task /path/to/task.json
```

The installed console script is equivalent:

```bash
devflow run --workspace /path/to/repository --task /path/to/task.json
```

The workspace must be the top-level directory of a Git repository with a valid `HEAD`. In V0.1,
target-project verification is deliberately restricted to `pytest` and `ruff check`; target Python
dependencies must already be available in the active environment. Docker sandboxing and isolated
per-task environments are deferred to later phases.

The command emits one JSON evidence bundle containing:

- terminal task state and state-transition history;
- actual Git changed files;
- Developer execution evidence;
- deterministic verification history;
- semantic review decisions;
- targeted repair attempts and terminal failures;
- configured Agent model identifiers;
- measured Developer/Repair token usage and latency.

A model saying "done" never marks the task successful. The terminal `SUCCEEDED` state is reachable
only after deterministic verification and independent semantic review both pass.

See `../docs/DEVELOPMENT_PLAN.md` and `../docs/PROGRESS.md` for the staged roadmap and acceptance
ledger.
