# DevFlow Architecture

## 1. System context

```text
                        User / Web UI
                             |
                             v
                        FastAPI API
                             |
                             v
                       DevFlow Runtime
                             |
          +------------------+------------------+
          |                  |                  |
          v                  v                  v
      Agent Layer       Workspace Layer   Verification Layer
          |                  |                  |
          v                  v                  v
   SiliconFlow Driver   Local Git Repo     pytest / ruff
          |              / Worktree        sandbox later
          v                  |
   SiliconFlow Models        v
                         Git / GitHub
```

DevFlow owns the control plane. SiliconFlow models are workers behind a provider interface and never become the authority for runtime state.

---

## 2. Main data flow

### V0.1

```text
User Requirement
      |
      v
Planner Agent
      |
      v
Validated TaskContract
      |
      v
Developer Agent <---- controlled repository tools
      |
      v
Actual Git Diff
      |
      v
Scope Enforcement
      |
      v
Deterministic Verification
      |
      +---- fail ----> Failure Classifier -> Repair Agent --+
      |                                                   |
      +<--------------------------------------------------+
      |
      v
Independent Reviewer
      |
      +---- changes requested -> Repair Agent ------------+
      |
      v
SUCCEEDED
```

### V0.2+

```text
Requirement
    |
    v
Planner -> Task DAG -> Scheduler
                       |
          +------------+------------+
          |                         |
          v                         v
      Worktree A                  Worktree B
      Developer A                Developer B
          |                         |
          v                         v
      Verification              Verification
          |                         |
          +------------+------------+
                       v
                 Merge Queue
                       |
                       v
                  Git branch/PR
```

---

## 3. Component responsibilities

### API layer

Owns:

- Projects/runs/tasks HTTP API.
- Input validation.
- SSE status/log streaming later.

Does not own orchestration logic.

### Runtime layer

Owns:

- Task state machine.
- Orchestration order.
- Retry limits.
- Failure classification.
- DAG scheduling in V0.2.

### Agent layer

Owns role prompts and role-specific input/output transformation:

- Planner.
- Developer.
- Reviewer.
- Repair.

It does not directly own provider credentials or filesystem access.

### Provider layer

Owns:

- SiliconFlow API client.
- Request/response normalization.
- Timeout/provider error mapping.
- Usage and latency extraction.

Runtime code depends on `AgentDriver`, not on SiliconFlow SDK details.

### Workspace layer

Owns:

- Git clone/fetch.
- Base commit tracking.
- Worktree creation/removal in V0.2.
- File read/search/write tool implementations.
- Scope checks.
- Git diff/status/commit operations.

### Verification layer

Owns:

- Deterministic commands.
- Exit codes/stdout/stderr/timeouts.
- Verification evidence.
- Docker sandbox later.

### Persistence layer

Owns durable project/run/task/agent-run/verification state. SQLite may be used for the first local implementation; PostgreSQL is the target persistent store.

---

## 4. Security boundaries

```text
SiliconFlow Model
      |
      | structured response / tool request
      v
DevFlow Permission Gate
      |
      +--> Read allowed context
      +--> Mutate only writable scope
      +--> Run only allow-listed commands
      v
Isolated Workspace
```

Rules:

- Models never receive GitHub tokens, SSH keys, `.env`, or provider API keys.
- File writes are path-normalized and scope checked.
- Golden/read-only tests are protected by contract and verified again from Git diff.
- Shell/process execution is allow-listed and timed out; Docker isolation arrives in V0.3.
- Agent loops and repair loops are bounded.

---

## 5. Backend target structure

```text
backend/
  app/
    api/
    agents/
      base.py
      planner.py
      developer.py
      reviewer.py
      repair.py
    providers/
      siliconflow.py
    runtime/
      orchestrator.py
      state_machine.py
      failure_classifier.py
      scheduler.py          # V0.2
    workspace/
      repository.py
      git.py
      scope.py
      worktree.py           # V0.2
    verification/
      verifier.py
      sandbox.py            # V0.3
    context/
      builder.py            # V0.3
    models/
    db/
  tests/
  pyproject.toml
```

Frontend is added only after V0.1 runtime behavior stabilizes.

---

## 6. Frontend target structure

```text
frontend/
  src/
    pages/
      Projects.tsx
      NewRun.tsx
      RunDashboard.tsx
      TaskDetail.tsx
    components/
    api/
    types/
```

The UI is an observability/control surface for the runtime, not the core runtime itself.

---

## 7. Key architectural decisions

### ADR-001 — GitHub remote, local execution

GitHub remains the remote source of truth. Agents operate against managed local repositories/worktrees because coding requires efficient search, diff, test, lint, build, and sandbox operations.

### ADR-002 — Explicit runtime instead of LangGraph core

The project implements its own task state machine and DAG scheduler. This keeps orchestration/failure/recovery logic as an explicit engineering contribution rather than delegating it to a workflow framework.

### ADR-003 — Provider-independent agents

Agent roles depend on an `AgentDriver` abstraction. SiliconFlow is the first provider. Role-to-model mapping is runtime configuration.

### ADR-004 — Deterministic gate before semantic reviewer

Tests/lint/scope checks run before Reviewer Agent. An LLM cannot override a failed deterministic gate.

### ADR-005 — Single-task first

V0.1 uses a single task/workspace and establishes the evidence loop before DAG parallelism, Worktrees, Redis, distributed leases, sandboxing, or frontend work.

### ADR-006 — Structured control plane

Data that changes runtime behavior must be validated by Pydantic models. Free-form LLM prose may be logged but cannot directly drive task state transitions.
