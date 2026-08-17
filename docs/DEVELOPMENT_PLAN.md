# DevFlow Development Plan

## 1. Project definition

**DevFlow** is an evidence-driven multi-agent software engineering runtime. A user provides a GitHub repository and a development requirement. DevFlow prepares an isolated local workspace, asks different LLM-backed agents to plan and implement the work, verifies the resulting repository state with deterministic checks, obtains an independent semantic review, performs targeted repair when necessary, and finally produces auditable Git commits / pull requests.

The LLMs used by DevFlow are provided through **SiliconFlow**. One model configuration corresponds to one agent role, but the runtime must not hard-code a specific model. Agent roles and model identifiers are configuration.

The system itself—not the model—owns filesystem, Git, process execution, sandbox, credentials, scheduling, and verification capabilities.

### Core principle

> **Agents propose; evidence decides.**

An agent saying “done” never marks a task as successful. Success requires repository-state evidence and explicit gates.

---

## 2. Final product experience

The target product is a web application with the following workflow:

1. User creates/selects a project.
2. User supplies a GitHub repository URL and development requirement.
3. DevFlow clones/fetches the repository into a managed local workspace.
4. Planner Agent converts the requirement into structured task contracts and, later, a Task DAG.
5. Developer Agent works only inside an isolated task workspace.
6. DevFlow inspects the actual Git diff and rejects out-of-scope modifications.
7. Deterministic verification runs (`pytest`, `ruff`, later `mypy`, build checks, etc.).
8. If verification fails, a Failure Classifier creates targeted repair context for a Repair Agent.
9. If hard verification passes, an independent Reviewer Agent performs semantic/architectural review.
10. Passing tasks are committed and, in the multi-task version, enter a merge queue.
11. The completed run produces a branch / Draft PR and a full execution evidence trail.

### Target dashboard

The web UI should eventually show:

- Project and repository.
- Current run status and overall progress.
- Task DAG and per-task state.
- Agent/model assigned to each task.
- Changed files and Git diff.
- Scope check result.
- Test/lint/type-check/build evidence.
- Reviewer decision and issues.
- Repair attempts and failure taxonomy.
- Token usage, latency, retries, first-pass success rate.
- Final commit/branch/PR information.

---

## 3. Repository and execution model

DevFlow uses two different repository concepts.

### 3.1 GitHub = remote source of truth

GitHub is responsible for:

- Remote repository identity.
- Default branch and base commit.
- Durable Git history.
- Final feature branches / Draft PRs.
- CI status and review evidence in later versions.

### 3.2 Local workspace = execution environment

Agents do **not** edit GitHub files one-by-one through the GitHub API during normal coding.

```text
GitHub repository
      |
      v
git clone / git fetch
      |
      v
Managed local repository
      |
      v
Task worktree / workspace
      |
      v
Developer Agent tool loop
      |
      v
Git diff + verification
      |
      v
commit / push / Draft PR
      |
      v
GitHub
```

In V0.1 a single managed workspace is acceptable. Git Worktree isolation is introduced in V0.2 when parallel tasks are added.

---

## 4. Agent roles

### 4.1 Planner Agent

Responsibilities:

- Understand the user requirement and repository context.
- Produce structured `TaskContract` objects.
- Define acceptance criteria and allowed write scope.
- In V0.2, produce task dependencies / DAG.

Planner output must be schema-validated before use.

### 4.2 Developer Agent

Responsibilities:

- Read only the context exposed by DevFlow tools.
- Implement one `TaskContract`.
- Use controlled file/search/patch tools instead of unrestricted host access.
- Never decide whether its own work is successful.

### 4.3 Reviewer Agent

Responsibilities:

- Review `TaskContract + Git diff + deterministic verification evidence`.
- Judge semantic correctness, architecture constraints, hidden logical errors, and code-quality concerns not captured by tests.
- Return a structured `PASS` or `CHANGES_REQUESTED` decision.

The reviewer does not replace deterministic tests.

### 4.4 Repair Agent

Responsibilities:

- Receive the original task plus targeted failure evidence.
- Repair the existing implementation instead of regenerating the complete module.
- Respect the same write scope as the Developer Agent.

The Repair Agent may reuse the same underlying SiliconFlow model as the Developer Agent.

---

## 5. SiliconFlow integration design

### 5.1 Provider abstraction

Business logic must not call SiliconFlow directly from Planner/Developer/Reviewer classes.

```text
PlannerAgent / DeveloperAgent / ReviewerAgent / RepairAgent
                         |
                         v
                    AgentDriver
                         |
                         v
                SiliconFlowDriver
                         |
                         v
                 SiliconFlow API
```

Target interface:

```python
class AgentDriver(Protocol):
    async def complete(self, request: AgentRequest) -> AgentResponse:
        ...
```

`SiliconFlowDriver` uses an OpenAI-compatible asynchronous client. Secrets must never be committed. The repository contains only `.env.example`; `.env` is ignored.

### 5.2 Role-to-model configuration

Models are configured rather than hard-coded:

```yaml
agents:
  planner:
    provider: siliconflow
    model: <planner-model>
  developer:
    provider: siliconflow
    model: <coding-model>
  reviewer:
    provider: siliconflow
    model: <review-model>
  repair:
    provider: siliconflow
    model: <coding-model>
```

This allows later evaluation of different model-role combinations without changing runtime code.

### 5.3 Structured output

Agent outputs that affect control flow must pass Pydantic validation.

Examples:

- `TaskPlan`
- `TaskContract`
- `ReviewDecision`
- `AgentResult`
- `FailureReport`

Invalid model output becomes `INVALID_AGENT_OUTPUT`; the runtime may perform a bounded schema-repair retry.

### 5.4 Tool boundary

Models never receive GitHub credentials or unrestricted host shell access.

Developer-facing tools will be introduced behind a permission gate, e.g.:

- `list_files`
- `read_file`
- `search_code`
- `write_file`
- `apply_patch`
- `run_diagnostics` (later restricted/sandboxed)

Every mutating operation must be checked against the task's writable scope.

---

## 6. Task Contract

`TaskContract` is the main contract between planning and execution.

Target V0.1 fields:

```text
task_id
objective
readable_files
writable_files
readonly_files
acceptance_criteria
verification_commands
max_retries
```

Later fields may include:

```text
dependencies
priority
risk_level
base_commit
expected_artifacts
```

### Scope rules

After an agent edits the repository, DevFlow reads the actual changed paths from Git.

If a changed path is outside `writable_files`, the run is rejected with:

```text
SCOPE_VIOLATION
```

Golden / read-only tests must never be writable by the Developer or Repair Agent.

---

## 7. Runtime state machine

V0.1 task lifecycle:

```text
PENDING
   |
   v
RUNNING
   |
   v
SCOPE_CHECK
   |------ scope violation ------> FAILED
   v
VERIFYING
   |------ verification fail ----> REPAIRING
   |                                  |
   |<---------------------------------|
   v
REVIEWING
   |------ changes requested ----> REPAIRING
   v
SUCCEEDED
```

Bounded retry is mandatory. A task may not loop forever.

Initial task states:

- `PENDING`
- `RUNNING`
- `VERIFYING`
- `REVIEWING`
- `REPAIRING`
- `SUCCEEDED`
- `FAILED`

V0.2 adds `READY` and `BLOCKED` for DAG scheduling.

---

## 8. Verification architecture

Verification has two layers.

### Layer A — deterministic hard gate

V0.1:

1. Git scope-integrity check.
2. `pytest`.
3. `ruff`.

Later:

4. `mypy`.
5. Build/package checks.
6. Project-specific verification commands.
7. Sandboxed execution.

Hard-gate failures cannot be overridden by an LLM reviewer.

### Layer B — independent semantic review

Only after hard checks pass:

```text
TaskContract
+ Git diff
+ verification evidence
        |
        v
Independent Reviewer
        |
        +--> PASS
        |
        +--> CHANGES_REQUESTED
```

---

## 9. Failure taxonomy

V0.1 should define the taxonomy even if only a subset is fully automated:

- `MODEL_TIMEOUT`
- `RATE_LIMIT`
- `INVALID_AGENT_OUTPUT`
- `TOOL_FAILURE`
- `SCOPE_VIOLATION`
- `TEST_FAILURE`
- `LINT_FAILURE`
- `REVIEW_REJECTED`
- `CONTEXT_OVERFLOW`
- `MERGE_CONFLICT` (V0.2)
- `SANDBOX_TIMEOUT` (V0.3)

Repair must use targeted failure evidence instead of blindly retrying the full original prompt.

---

## 10. Backend plan

### Target stack

- Python 3.11+
- FastAPI
- Pydantic v2
- SQLAlchemy 2
- SQLite in earliest local MVP; PostgreSQL before V1.0
- OpenAI-compatible async client for SiliconFlow
- Git CLI wrapped by repository/workspace services
- pytest
- ruff
- mypy later
- Docker in V0.3
- Redis + Dramatiq in V0.3 when workers become distributed

### Backend boundaries

```text
api/           HTTP + SSE interfaces
agents/        planner/developer/reviewer/repair role logic
providers/     SiliconFlow implementation of AgentDriver
runtime/       orchestrator, task state machine, scheduler, failure handling
workspace/     clone/fetch, Git, worktrees, scope enforcement
verification/  deterministic verification and sandbox execution
context/       context packet construction (V0.3)
db/            persistence models and repositories
```

Do not use LangGraph for the core scheduler/state machine. Runtime orchestration is part of the project's engineering contribution and should be implemented explicitly.

---

## 11. Frontend plan

Frontend is deferred until the backend execution loop is stable.

### Target stack

- React
- TypeScript
- Vite
- React Router
- TanStack Query
- Tailwind CSS
- shadcn/ui
- SSE for run/log updates in the first frontend version

### Target pages

1. **Projects** — known repositories/projects.
2. **New Run** — repository, requirement, agent/model configuration.
3. **Run Dashboard** — Task DAG, run status, agents, verification, metrics.
4. **Task Detail** — TaskContract, diff, checks, reviewer result, repair history.

WebSocket is not required initially; SSE is sufficient for backend-to-frontend status/log streaming.

---

## 12. Persistence plan

Initial logical entities:

### `projects`

- id
- name
- repository_url
- default_branch
- created_at

### `tasks`

- id
- project_id
- objective
- status
- writable_scope
- readonly_scope
- acceptance_criteria
- verification_commands
- max_retries

### `runs`

- id
- project_id
- status
- base_commit
- started_at
- finished_at

### `agent_runs`

- id
- task_id
- role
- provider
- model
- status
- started_at
- finished_at
- prompt_tokens
- completion_tokens
- error_type

### `verification_runs`

- id
- task_id
- check_type
- command
- exit_code
- stdout
- stderr
- duration_ms

V0.3 may add `lease_owner`, `lease_until`, `heartbeat_at`, and `run_token`.

---

# 13. Implementation roadmap

The project is intentionally incremental. Later phases must not be implemented before earlier acceptance criteria are met.

## Phase 0 — Planning baseline

**Goal:** freeze the initial architecture before application code.

Deliverables:

- [x] `README.md`
- [x] `docs/DEVELOPMENT_PLAN.md`
- [x] `docs/ARCHITECTURE.md`
- [ ] Review/freeze V0.1 scope

Exit criteria:

- Product flow is explicit.
- Component responsibilities are explicit.
- V0.1 scope is bounded.
- No application code has been started prematurely.

---

## Phase 1 — V0.1 Single Task Evidence Loop

**Goal:** make one task go from contract to verified completion.

### Step 1.1 — Project skeleton and quality baseline

Implement:

- Backend Python package structure.
- `pyproject.toml`.
- `.gitignore` and `.env.example`.
- pytest / ruff configuration.
- Base settings loader.

Acceptance:

- `pytest` runs successfully.
- `ruff check .` runs successfully.
- No secrets are stored in Git.

### Step 1.2 — Core schemas

Implement Pydantic models:

- `TaskContract`
- `AgentRequest`
- `AgentResponse`
- `ReviewDecision`
- `VerificationResult`
- `FailureReport`

Acceptance:

- Valid examples parse.
- Invalid writable/read-only/scope payloads are rejected.
- Unit tests cover schema validation.

### Step 1.3 — SiliconFlow provider

Implement:

- `AgentDriver` protocol/ABC.
- `SiliconFlowDriver`.
- Timeouts and provider error normalization.
- Usage/latency capture.
- Role-to-model configuration.

Acceptance:

- Driver can make one configured completion call when API key is available.
- Unit tests use a fake transport/client; normal test suite does not require paid API calls.
- Provider failures map to project error types.

### Step 1.4 — Planner structured output

Implement:

- Planner prompt contract.
- Structured task output.
- Pydantic parsing.
- Bounded schema-repair retry.

Acceptance:

- A simple requirement produces a valid TaskContract.
- Invalid structured output does not enter execution.

### Step 1.5 — Workspace and Git scope enforcement

Implement:

- Local repository/workspace abstraction.
- Changed-file collection from Git.
- Glob-aware writable-scope matching.
- Read-only / golden-test protection.

Acceptance:

- Allowed source modification passes.
- Test-file tampering triggers `SCOPE_VIOLATION`.
- Out-of-scope files cannot silently proceed to verification.

### Step 1.6 — Developer tool loop

Implement controlled tools:

- list files
- read file
- search code
- write/apply patch

Implement bounded Developer Agent loop.

Acceptance:

- Developer can modify a toy repository task through tools.
- Tool path traversal/out-of-scope writes are rejected.
- Loop has maximum iterations/time budget.

### Step 1.7 — Deterministic verifier

Implement:

- verification-command runner
- pytest adapter
- ruff adapter
- stdout/stderr/exit-code capture
- timeout

Acceptance:

- Passing project returns hard-gate PASS.
- Failing test returns `TEST_FAILURE` with useful stderr.
- Lint failure is distinguishable from test failure.

### Step 1.8 — Independent reviewer

Implement:

- Review input packet = task + diff + verification evidence.
- Structured `PASS` / `CHANGES_REQUESTED` output.

Acceptance:

- Reviewer runs only after hard verification passes.
- Review output is schema validated.

### Step 1.9 — Targeted repair loop

Implement:

- Failure classification.
- Repair prompt containing original task + targeted evidence.
- Retry counter and terminal failure.

Acceptance:

- At least one demo fails on first attempt, receives targeted repair, and passes later.
- Retry count is bounded.

### Step 1.10 — Orchestrator + CLI/API demo

Implement single-task orchestrator:

```text
Task -> Developer -> Scope -> Verify -> Review -> Repair/Success
```

Expose first via CLI; add minimal FastAPI endpoints only where useful.

Acceptance:

- One command/API request runs the complete V0.1 loop.
- Final output includes changed files, checks, reviewer decision, attempts, model/usage metadata.

**V0.1 exit criterion:** a reproducible single-task demo works end-to-end and produces evidence that can be inspected independently of the LLM's self-report.

---

## Phase 2 — V0.2 True Multi-Agent Runtime

**Goal:** support dependent and parallel tasks safely.

Implement in this order:

1. Task DAG representation.
2. `READY/RUNNING/SUCCEEDED/FAILED/BLOCKED` scheduler.
3. Git Worktree per task.
4. Parallel worker execution.
5. Topological merge queue.
6. Merge conflict classification.
7. Integration/Human Gate strategy.

Acceptance:

- Two independent tasks can run concurrently in isolated worktrees.
- One task cannot modify another task's workspace.
- Dependency-blocked tasks do not start early.
- Merge order follows DAG constraints.

Do **not** begin with speculative rebase. Start with deterministic topological merge and explicit conflict failure; integration-agent automation is incremental.

---

## Phase 3 — V0.3 Safety, Context and Reliability

**Goal:** move from a local orchestrator toward a robust runtime.

Implement:

1. Docker verification sandbox.
2. CPU/memory/network/time limits.
3. Context Packet Builder.
4. AST/import-aware relevant-code extraction.
5. PostgreSQL persistence.
6. Redis + Dramatiq workers.
7. Lease + heartbeat.
8. `run_token` stale-write protection.
9. Structured run/event logs.

Acceptance:

- Infinite/expensive test execution is bounded.
- Worker death can be recovered without stale result corruption.
- Context-packet mode can be compared with larger-context mode.

---

## Phase 4 — V1.0 Productization

**Goal:** make the runtime easy to demonstrate and evaluate.

Implement:

1. React/TypeScript UI.
2. Project / New Run / Dashboard / Task Detail pages.
3. SSE live status/log updates.
4. DAG visualization.
5. Diff viewer.
6. Run metrics.
7. GitHub branch + Draft PR integration.
8. Benchmark/demo suite.

Acceptance:

- User can start a run from the browser.
- Execution can be understood without reading backend logs.
- A completed run links to its Git branch/PR and evidence.

---

## 14. Required demo cases

### Demo A — Normal success

Developer implements task -> hard checks pass -> reviewer passes -> success.

### Demo B — Test tampering / scope violation

Developer modifies a protected test -> Git diff detects it -> `SCOPE_VIOLATION` -> task rejected/repaired.

### Demo C — Tests pass but semantic review fails

Hard checks pass -> Reviewer finds missing semantic requirement -> targeted repair -> re-verification -> pass.

### Demo D — Invalid agent structure

Agent produces invalid JSON/schema -> validation error -> bounded schema repair -> valid result or terminal failure.

### Demo E — Parallel conflict (V0.2)

Two tasks develop independently -> worktree isolation succeeds -> merge conflict is explicitly classified and handled without corrupting main.

---

## 15. Evaluation plan

At least one reproducible benchmark set should record:

- Task success rate.
- First-pass success rate.
- Success after repair.
- Average retry count.
- Reviewer rejection rate after deterministic checks pass.
- Scope violations detected.
- Mean/median task latency.
- Prompt/completion token usage.
- Estimated model cost when available.

Useful ablations:

- With reviewer vs without reviewer.
- Naive retry vs targeted repair.
- Large/full context vs Context Packet.
- Different role-to-model assignments.

Do not write claims such as “90%+ improvement” in README/resume until measured by this project.

---

## 16. Development rules

1. **Plan before code.** Architecture changes update documentation first.
2. **One phase at a time.** Do not implement V0.2/V0.3 features to make V0.1 look more advanced.
3. **Tests accompany behavior.** Core runtime rules require unit tests.
4. **No model self-certification.** Agent text cannot set `SUCCEEDED` directly.
5. **No credentials in prompts/repository.** Provider/GitHub secrets stay outside model context.
6. **Single source of runtime truth.** Use actual Git/file/process state, not chat history, as evidence.
7. **Bound every loop.** Agent/tool/repair loops need retry/iteration/time limits.
8. **Structured control plane.** State transitions use validated data models, not free-form prose.
9. **Record failures, do not hide them.** Demo and metrics include failed attempts.
10. **Keep Git history meaningful.** Each implementation step should be reviewable as a coherent change.

---

## 17. Immediate execution order

The next work must follow this exact sequence unless the plan is deliberately amended:

```text
CURRENT: Phase 0 — planning baseline

NEXT:
1. Review/freeze this plan.
2. Phase 1 / Step 1.1 — project skeleton + quality baseline.
3. Step 1.2 — schemas.
4. Step 1.3 — SiliconFlow provider.
5. Step 1.4 — Planner.
6. Step 1.5 — workspace/scope enforcement.
7. Step 1.6 — Developer tool loop.
8. Step 1.7 — verifier.
9. Step 1.8 — reviewer.
10. Step 1.9 — repair.
11. Step 1.10 — end-to-end V0.1 demo.
```

**No DAG, Worktree parallelism, Redis, Docker sandbox, or frontend implementation should begin before V0.1 exits successfully.**

---

## 18. Definition of V0.1 Done

V0.1 is complete only when all are true:

- A user requirement can become a validated TaskContract.
- A SiliconFlow-backed Developer Agent can edit a controlled toy repository through DevFlow tools.
- Git-based scope enforcement catches protected/out-of-scope modifications.
- pytest/ruff results are captured as deterministic evidence.
- An independent SiliconFlow-backed Reviewer can pass or reject the verified diff.
- Failure evidence can drive a bounded targeted repair loop.
- The full execution is reproducible from CLI/minimal API.
- Run output records model identity, attempts, changed files, verification results, and reviewer decision.
- Automated tests cover the core runtime rules.

Only after this point does implementation move to V0.2.
