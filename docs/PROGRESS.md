# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. The development plan defines *what* should be built; this file records *what has actually passed acceptance*.

## Current position

- Phase: **Phase 1 — V0.1 Single Task Evidence Loop**
- Completed step: **Step 1.8 — Independent reviewer**
- Next step: **Step 1.9 — Targeted repair loop**
- Step 1.9 status: **NOT STARTED**

## Step 1.1 — ACCEPTED

Merged through PR #1: `Phase 1 Step 1.1: initialize backend quality baseline`.

Delivered:

- Python 3.11+ backend package skeleton.
- `backend/pyproject.toml` with package metadata and quality-tool configuration.
- `.gitignore` and secret-safe `.env.example`.
- Typed Pydantic Settings baseline.
- Settings unit tests.
- Ruff lint baseline.
- GitHub Actions backend quality workflow.

Acceptance evidence:

- Dependency installation: **PASS**
- `ruff check .`: **PASS**
- `pytest`: **PASS**
- GitHub Actions `Backend Quality`: **SUCCESS**
- No SiliconFlow call, Agent implementation, orchestration, or frontend was introduced early.

## Step 1.2 — ACCEPTED

Merged through PR #2: `Phase 1 Step 1.2: add core runtime schemas`.

Delivered:

- `TaskContract` with validated repository-relative readable/writable/read-only scopes.
- Rejection of empty, duplicate, absolute, Windows-style, and `..` traversal scope patterns.
- Explicit writable/read-only exact-overlap rejection.
- Provider-neutral `AgentRequest` and `AgentResponse` schemas plus messages, roles, and token usage.
- `ReviewDecision` with consistent `PASS` / `CHANGES_REQUESTED` semantics.
- `VerificationResult` and `CheckResult` with deterministic aggregate-result consistency.
- `FailureReport` and the planned failure taxonomy.
- Core-schema unit tests.

Acceptance evidence:

- `ruff check .`: **PASS**
- `pytest`: **21 passed**
- GitHub Actions `Backend Quality`: **SUCCESS**
- Invalid scope and inconsistent review/verification payloads are rejected by Pydantic validation.
- No SiliconFlow API call, Planner behavior, workspace implementation, or execution loop was introduced early.

## Step 1.3 — ACCEPTED

Merged through PR #3: `Phase 1 Step 1.3: add SiliconFlow provider boundary`.

Delivered:

- Provider-neutral `AgentDriver` protocol.
- `SiliconFlowDriver` backed by the OpenAI-compatible async client.
- Configurable SiliconFlow base URL, timeout, SDK retry count, and API key loading.
- Role-to-model configuration for Planner, Developer, Reviewer, and Repair roles.
- Runtime-stable provider error codes for timeout, rate limit, authentication, permission, bad request, connection, service unavailable, and unknown failures.
- Mapping from provider failures into the existing `FailureReport` taxonomy.
- Normalized response content, model, finish reason, token usage, and latency metadata.
- Fake-client tests covering success, missing usage, empty choices, timeout, 429, 401, and 503 behavior.
- `.env.example` documentation for provider/model settings without committing a real key.

Acceptance evidence:

- OpenAI-compatible SDK installation: **PASS** (`openai 2.54.0` in CI).
- `ruff check .`: **PASS** after correcting the single `SIM108` lint finding.
- `pytest`: **31 passed**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- CI made **no real SiliconFlow API call** and required no paid API key.
- No Planner behavior, workspace/Git tooling, or Agent execution loop was introduced early.

## Step 1.4 — ACCEPTED

Merged through PR #4: `Phase 1 Step 1.4: add Planner structured output gate`.

Delivered:

- `PlannerAgent` implementing the first real Agent behavior in DevFlow.
- Natural-language development requirement to one V0.1 `TaskContract` conversion through the provider-neutral `AgentDriver`.
- Planner system prompt generated against the current Pydantic `TaskContract` JSON Schema.
- Strict JSON-only machine output; Markdown fences, prose, missing fields, invalid scopes, and extra keys cannot bypass Pydantic validation.
- Optional caller-supplied repository context without repository or Git access inside the Planner.
- Bounded schema-repair retry with a configurable 0–3 attempt budget and default of one repair attempt.
- Schema repair receives the previous invalid output plus Pydantic validation evidence and runs at temperature `0.0`.
- `InvalidPlannerOutputError` carrying a normalized `FailureReport(INVALID_AGENT_OUTPUT)` when the schema-repair budget is exhausted.
- Defensive clipping of invalid-output and validation-error evidence before it is placed into repair prompts/failure evidence.
- Explicit Ruff first-party package configuration for the flat `backend/app` layout.
- Unit tests covering first-pass success, successful repair, exhausted repair budget, zero repair budget, empty requirement rejection, caller-supplied repository context, provider-error propagation, and bounded Planner configuration.

Acceptance evidence:

- Strict `ruff check .`: **PASS**.
- `pytest`: **43 passed in 0.84s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Invalid Planner output cannot escape as a `TaskContract`; it is either repaired within the configured budget or converted to terminal `INVALID_AGENT_OUTPUT` evidence.
- Provider failures propagate separately from schema-validation failures.
- CI made **no real SiliconFlow API call** and required no paid API key.
- No repository clone/read/write, Git tooling, Developer loop, target-repository verification, Reviewer behavior, or orchestration was introduced early.

## Step 1.5 — ACCEPTED

Merged through PR #5: `Phase 1 Step 1.5: add workspace and Git scope enforcement`.

Delivered:

- `LocalGitWorkspace` as the managed local repository boundary and Git-state source of truth.
- Workspace roots must be existing Git top-level directories with a valid `HEAD` commit.
- Safe repository-relative POSIX path resolution with rejection of empty paths, absolute paths, backslashes, Windows drive prefixes, `..` traversal, and existing symlink escapes.
- Changed-file collection from tracked unstaged changes, staged changes, and untracked non-ignored files relative to `HEAD`.
- Git rename detection is disabled for scope evidence so moving a protected file is represented as deletion of the protected source plus addition of the destination.
- Slash-aware V0.1 glob matching for `*`, `?`, `**`, and `**/` patterns.
- Read-only scope takes precedence over writable scope, including cases where a broad writable glob would otherwise include a protected test path.
- Structured `ScopeCheckResult`, `ScopeViolation`, and `ScopeViolationKind` evidence.
- Scope failures normalize to terminal `FailureReport(SCOPE_VIOLATION)` and cannot silently proceed to later verification.
- Integration-style tests create real temporary Git repositories and exercise tracked, staged, untracked, rename, scope, and path-boundary behavior.

Acceptance evidence:

- Strict `ruff check .`: **PASS**.
- `pytest`: **55 passed in 1.08s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Allowed nested source modification passes the scope gate.
- Protected test modification produces `READ_ONLY` scope evidence and `SCOPE_VIOLATION`.
- Out-of-scope file modification produces `OUT_OF_SCOPE` evidence and `SCOPE_VIOLATION`.
- Broad writable scopes cannot override read-only / golden-test protection.
- Renaming a protected test cannot bypass the gate because the protected source deletion remains visible in Git evidence.
- Repository-path traversal, absolute paths, backslash paths, Windows drive prefixes, and symlink escapes are rejected at the workspace boundary.
- No Developer Agent tool loop, model-controlled file mutation, target-project verification command execution, Reviewer/Repair behavior, worktree scheduling, Redis, Docker, or frontend was introduced early.

## Step 1.6 — ACCEPTED

Merged through PR #6: `Phase 1 Step 1.6: add bounded Developer tool loop`.

Delivered:

- Provider-neutral Function Calling schemas: `ToolDefinition`, `ToolCall`, `ToolExecutionResult`, and stable tool error codes.
- `AgentRequest`, `AgentResponse`, and message history support assistant tool calls and `role=tool` observations without coupling Agent logic to SiliconFlow.
- `SiliconFlowDriver` adapts OpenAI-compatible `tools` requests and provider `tool_calls` responses into the provider-neutral runtime protocol while preserving ordinary text-completion compatibility.
- Controlled repository tools: `list_files`, `read_file`, `search_code`, `write_file`, and exact-context `apply_patch`; no unrestricted shell is exposed.
- Read operations reveal only task-visible scopes; mutations are checked against writable/read-only scope before filesystem changes.
- `.git` internals are permanently denied, including path aliases such as `./.git/config`, even under a broad `**` writable contract.
- Symbolic-link traversal is denied at the workspace path boundary so a writable alias cannot redirect a mutation into protected files.
- `apply_patch` requires `old_text` to occur exactly once, preventing ambiguous multi-location replacement.
- `DeveloperAgent` implements a bounded model/tool/observation loop with maximum iterations, wall-clock duration, and per-turn tool-call fan-out.
- `DeveloperRunResult` records stop reason, tool calls, actual Git changed files, usage, latency, and final model message but intentionally contains no task-success verdict.
- Integration tests use real temporary Git repositories and fake model/provider responses; CI never needs a real SiliconFlow API key.

Acceptance evidence:

- Strict `ruff check .`: **PASS**.
- `pytest`: **67 passed in 1.46s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- Native Function Calling request/response serialization is covered with a fake SiliconFlow/OpenAI-compatible client.
- A bounded Developer loop can read a real repository file, apply a controlled patch, return an observation to the model, and leave the actual change visible through Git evidence.
- Protected test writes and out-of-scope writes are rejected before disk mutation.
- `.git` direct access and `./.git/...` alias access are rejected; internal symbolic-link write aliases cannot mutate protected tests.
- Excess per-turn tool fan-out is stopped before any of those tool calls execute.
- Iteration and time budgets terminate the loop deterministically.
- A model final message only stops Developer execution; it does **not** mark the task as passed or successful.
- No target-repository verification command was executed, no real SiliconFlow API call was made, and no Reviewer, Repair, orchestrator, DAG/worktree, Redis, Docker, or frontend behavior was introduced early.

## Step 1.7 — ACCEPTED

Merged through PR #7: `Phase 1 Step 1.7: add deterministic verifier hard gate`.

Delivered:

- `DeterministicVerifier` with actual Git scope integrity as the mandatory first verification gate.
- Scope violations fail closed before any target-project process is started.
- Bounded verification-command execution with per-command timeout, maximum-command budget, stdout/stderr capture, exit codes, duration, and output clipping.
- Explicit pytest and Ruff adapters/classification: failed tests become `TEST_FAILURE`; failed lint checks become `LINT_FAILURE`.
- Failed deterministic checks convert into structured `FailureReport` evidence for later targeted repair; test/lint failures are retryable while scope and unsafe-command failures are not.
- V0.1 process execution is deliberately restricted to pytest and `ruff check`, including their `python -m` forms. Arbitrary custom commands remain deferred until sandbox execution is available.
- Verification always uses `shell=False` and normalizes allowed tools to the current runtime interpreter via `sys.executable -m ...`, avoiding reliance on a same-name executable found through `PATH`.
- Verification arguments are workspace-bound: absolute paths, Windows drive paths, and `..` traversal are rejected, including path-like option values such as `--rootdir=/tmp`.
- Integration-style tests run against real temporary Git repositories and launch real pytest/Ruff subprocesses.
- Developer Agent self-reports are never used as verification evidence; only repository state and deterministic process results determine this hard gate.

Acceptance evidence:

- Strict `ruff check .`: **PASS**.
- `pytest`: **76 passed in 2.86s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- A passing toy repository returns hard-gate `VerificationResult(passed=True)` after scope, pytest, and Ruff checks pass.
- A failing pytest run is distinguishable as `TEST_FAILURE` and preserves useful process output/exit-code evidence.
- A Ruff violation is distinguishable as `LINT_FAILURE` even when pytest passes.
- Protected/out-of-scope Git changes stop verification before subprocess execution.
- Unsupported commands such as `python -c ...` are rejected without executing their payload.
- Verification timeout is bounded and becomes structured `TOOL_FAILURE` evidence.
- `pytest ../outside_test.py` cannot execute an outside-workspace test, and absolute option paths such as `--rootdir=/tmp` are rejected before process start.
- No Reviewer, Repair, full Orchestrator, multi-task DAG/worktree scheduler, Redis, Docker sandbox, or frontend behavior was introduced early.

## Step 1.8 — ACCEPTED

Merged through PR #8: `Phase 1 Step 1.8: add independent semantic reviewer`.

Delivered:

- `ReviewerAgent` as a read-only semantic gate that can run only after deterministic verification has passed.
- Reviewer input is deliberately restricted to the validated `TaskContract`, a passing `VerificationResult`, and the actual HEAD-to-workspace Git diff; Developer conversation history is not supplied.
- Reviewer requests contain no tool definitions, so the Reviewer cannot mutate files, run shell commands, or operate Git through the Agent interface.
- `ReviewDecision` is Pydantic/schema validated: `PASS` requires zero issues and `CHANGES_REQUESTED` requires at least one concrete issue.
- Invalid Reviewer output uses bounded schema repair; exhaustion becomes terminal `FailureReport(INVALID_AGENT_OUTPUT)` with `FailureSource.REVIEW`.
- Repository content and Git diff are explicitly treated as untrusted data to reduce prompt-injection risk from code/comments embedded in the patch.
- `LocalGitWorkspace.unified_diff()` provides actual tracked changes plus reviewable patches for untracked text files, so newly created files cannot silently escape semantic review before `git add`.
- Empty diffs are rejected before a Reviewer model call.
- Reviewer tests use real temporary Git repositories and a fake model driver; CI does not require a SiliconFlow API key or consume model quota.

Acceptance evidence:

- Strict `ruff check .`: **PASS**.
- `pytest`: **84 passed in 4.15s**.
- GitHub Actions `Backend Quality`: **SUCCESS**.
- A failed `VerificationResult` prevents the Reviewer model from being called at all.
- Reviewer requests contain `tools=[]` and the repository diff remains unchanged before and after review.
- The Reviewer can return schema-valid `CHANGES_REQUESTED` for a semantic security bug (`verify_token` always returning true) even when supplied hard verification evidence is passing.
- Schema-invalid Reviewer output is either repaired within the configured budget or rejected as `INVALID_AGENT_OUTPUT`; it never enters task control flow as a valid decision.
- Untracked text files are included in semantic-review diff evidence.
- No Repair Agent, full Orchestrator, DAG/worktree scheduler, Redis, Docker sandbox, frontend, or real SiliconFlow API call was introduced early.

## Gate before Step 1.9

Step 1.9 may implement only the targeted Repair behavior required by the V0.1 single-task loop. Repair must consume the original validated `TaskContract` plus targeted failure evidence from deterministic verification (`FailureReport`) or semantic review (`ReviewDecision` issues), and must reuse the same controlled repository tools and writable/read-only boundaries as the Developer Agent. Repair attempts must be bounded by an explicit retry budget and must focus on the observed failure rather than blindly regenerating the whole implementation. Tests must demonstrate at least one first-attempt failure receiving targeted evidence and a later repair that can satisfy the relevant gate in isolation. Step 1.9 must not introduce the full end-to-end Orchestrator (Step 1.10), multi-task DAG/worktree scheduling, Redis, Docker sandboxing, or frontend features prematurely.
