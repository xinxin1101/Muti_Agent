# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*.

## Current position

- Phase: **Phase 1 — V0.1 Single Task Evidence Loop**
- Completed step: **Step 1.9 — Targeted repair loop**
- Next step: **Step 1.10 — Orchestrator + CLI/API demo**
- Step 1.10 status: **NOT STARTED**

## Step 1.1 — ACCEPTED

Merged through PR #1: `Phase 1 Step 1.1: initialize backend quality baseline`.

Delivered:
- Python 3.11+ backend package skeleton and `pyproject.toml`.
- Secret-safe `.gitignore` / `.env.example` and typed Settings baseline.
- pytest, Ruff, and GitHub Actions `Backend Quality` baseline.

Acceptance evidence:
- Dependency installation: **PASS**.
- `ruff check .`: **PASS**.
- `pytest`: **PASS**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 1.2 — ACCEPTED

Merged through PR #2: `Phase 1 Step 1.2: add core runtime schemas`.

Delivered:
- `TaskContract`, `AgentRequest`, `AgentResponse`, `ReviewDecision`, `VerificationResult`, and `FailureReport`.
- Scope/path validation and consistent review/verification state validation.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **21 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 1.3 — ACCEPTED

Merged through PR #3: `Phase 1 Step 1.3: add SiliconFlow provider boundary`.

Delivered:
- Provider-neutral `AgentDriver` and `SiliconFlowDriver`.
- Configurable role-to-model mapping, timeout, error normalization, token usage, and latency capture.
- Fake-client coverage; CI requires no paid API call.

Acceptance evidence:
- OpenAI-compatible SDK installation: **PASS** (`openai 2.54.0` in CI at acceptance time).
- `ruff check .`: **PASS**.
- `pytest`: **31 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.

## Step 1.4 — ACCEPTED

Merged through PR #4: `Phase 1 Step 1.4: add Planner structured output gate`.

Delivered:
- `PlannerAgent` converting one natural-language requirement into a validated V0.1 `TaskContract`.
- JSON-schema/Pydantic validation and bounded schema-repair retry.
- Invalid output becomes terminal `INVALID_AGENT_OUTPUT` rather than entering execution.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **43 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- No real SiliconFlow API call in CI.

## Step 1.5 — ACCEPTED

Merged through PR #5: `Phase 1 Step 1.5: add workspace and Git scope enforcement`.

Delivered:
- `LocalGitWorkspace` as the local Git source of truth.
- Safe repository-relative path resolution and `.git`/symlink/path-traversal boundaries.
- Actual changed-file collection and deny-first `ScopeEnforcer` with read-only/golden-test protection.
- Rename-resistant scope evidence.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **55 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Allowed source edits pass; read-only or out-of-scope edits become `SCOPE_VIOLATION`.

## Step 1.6 — ACCEPTED

Merged through PR #6: `Phase 1 Step 1.6: add bounded Developer tool loop`.

Delivered:
- Provider-neutral Function Calling schemas and SiliconFlow tool-call adaptation.
- Controlled repository tools: `list_files`, `read_file`, `search_code`, `write_file`, and exact-context `apply_patch`.
- Bounded `DeveloperAgent` loop with iteration/time/tool-fan-out limits.
- `DeveloperRunResult` records evidence but deliberately has no success verdict.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **67 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Protected/out-of-scope writes, `.git` aliases, and symlink write aliases are blocked.
- No real SiliconFlow API call in CI.

## Step 1.7 — ACCEPTED

Merged through PR #7: `Phase 1 Step 1.7: add deterministic verifier hard gate`.

Delivered:
- Scope-first `DeterministicVerifier`.
- Bounded pytest/Ruff execution with `shell=False`, timeout, output, exit-code, and duration evidence.
- Explicit `TEST_FAILURE`, `LINT_FAILURE`, `SCOPE_VIOLATION`, and tool/runtime failure classification.
- Workspace-bound command arguments and no arbitrary V0.1 shell execution.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **76 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Hard verification is based on repository/process evidence, never Developer self-report.

## Step 1.8 — ACCEPTED

Merged through PR #8: `Phase 1 Step 1.8: add independent semantic reviewer`.

Delivered:
- Read-only `ReviewerAgent` that runs only after a passing hard gate.
- Input packet restricted to validated `TaskContract` + actual Git diff + `VerificationResult`.
- Reviewer receives no write/shell/Git tools.
- Schema-validated `PASS` / `CHANGES_REQUESTED` with bounded structured-output repair.
- Actual unified diff includes untracked text files and is treated as untrusted repository data.

Acceptance evidence:
- `ruff check .`: **PASS**.
- `pytest`: **84 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Reviewer can reject a semantic bug even when deterministic checks are represented as passing.
- No real SiliconFlow API call in CI.

## Step 1.9 — ACCEPTED

Merged through PR #9: `Phase 1 Step 1.9: add failure-aware targeted repair loop`.

Delivered:
- `FailureClassifier` converts deterministic verification failures and semantic review rejection into structured repair evidence.
- Repairable evidence is intentionally limited to retryable `TEST_FAILURE`, `LINT_FAILURE`, and `REVIEW_REJECTED`.
- Repair selection is **fail-closed and all-or-nothing**: if a failure batch also contains a non-retryable/safety failure such as `SCOPE_VIOLATION`, no failure in that batch is sent to the Repair model.
- `RepairAgent` uses `AgentRole.REPAIR` and the same controlled `RepositoryToolbox` / writable/read-only boundaries as the Developer Agent.
- Repair prompts contain the original validated `TaskContract` plus targeted `FailureReport` evidence instead of replaying the original Developer conversation or blindly regenerating the task.
- Repair execution is bounded by iteration, wall-clock, tool-fan-out, and `TaskContract.max_retries` budgets.
- Retry exhaustion is checked **before model execution**, preserves the original root failure type, sets `retryable=False`, and appends `repair_attempts_exhausted` evidence.
- `RepairRunResult` records attempt number, failure types, tool activity, changed files, token usage, and latency, but intentionally has no task-success verdict.
- Failure evidence is clipped, and repository text embedded in stderr/review messages is treated as untrusted data rather than instructions.

Verification side-effect hardening discovered during Step 1.9:
- The first integration run exposed that pytest could create `tests/__pycache__/*.pyc`, polluting Git evidence and potentially causing a false read-only scope violation on the next gate.
- Verification now disables Python bytecode writes and pytest cacheprovider output.
- Mutating Ruff verification modes such as `ruff check --fix` / `--fix-only` are rejected before execution.
- A post-verification Git scope gate detects out-of-scope files created by verification commands themselves.

Acceptance evidence:
- Integration test performs a **real** first-pass pytest failure, converts it to `TEST_FAILURE`, feeds targeted evidence to Repair, applies one controlled patch, reruns the same deterministic verifier, and reaches PASS.
- Non-retryable safety failures cannot invoke Repair.
- Mixed retryable + safety failures fail closed instead of silently dropping the safety failure.
- Repair budget exhaustion occurs before an extra LLM call and keeps the original failure taxonomy.
- Pytest verification leaves no `.pytest_cache` / test `__pycache__` Git pollution in the covered V0.1 path.
- `ruff check --fix` cannot be used as a mutating verification command.
- Post-verification scope violations are detected.
- Strict `ruff check .`: **PASS**.
- `pytest`: **97 passed in 4.33s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- CI made **no real SiliconFlow API call**.
- No full single-task Orchestrator, CLI/API run entrypoint, DAG/worktree scheduler, Redis, Docker sandbox, or frontend behavior was introduced prematurely.

## Gate before Step 1.10

Step 1.10 may now compose the already accepted V0.1 components into one explicit single-task runtime:

```text
TaskContract
    ↓
DeveloperAgent
    ↓
DeterministicVerifier
    ├── hard failure + repairable evidence → RepairAgent → verify again
    └── PASS
          ↓
ReviewerAgent
    ├── CHANGES_REQUESTED → FailureClassifier → RepairAgent → verify again → review again
    └── PASS → SUCCEEDED
```

Step 1.10 must provide a bounded task state machine and auditable final run result. Retry attempts must use the Step 1.9 budget semantics and must terminate deterministically. The first runnable surface should be a CLI; minimal FastAPI endpoints may be added only where they materially help demonstrate the V0.1 loop. Final output must expose changed files, verification evidence, reviewer decision, repair attempts, model/usage metadata, and terminal failure evidence when unsuccessful.

Step 1.10 must **not** prematurely add V0.2/V0.3/V1.0 concerns: no multi-task DAG scheduling, parallel Git worktrees, merge queue, Redis/Dramatiq, Docker sandbox, Context Packet Builder, or React frontend yet.
