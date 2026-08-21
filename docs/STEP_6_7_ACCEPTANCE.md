# Step 6.7 — Full Autonomous Product E2E — Acceptance

## Status

**ACCEPTED / COMPLETE at implementation head `e4f12f2fe0f90b2d789f242f22d4b7b3a9126108`.**

Step 6.7 closes the product-wiring objective for Phase 6: one natural-language requirement can enter the browser-facing Product API, become a validated persisted multi-task DAG, execute independent roots, unlock a dependent task only after accepted integration, finalize from evidence, expose a reproducible Integration Diff, and select an evidence-bound GitHub Draft PR source.

The acceptance does **not** promote the deterministic test Planner, broker stub, or GitHub stub into production authority. They remove external nondeterminism from CI while the Git / PostgreSQL / reconciliation / completion / projection boundaries under test remain the production implementations.

---

## Acceptance path

`backend/tests/test_autonomous_multi_agent_e2e.py` proves:

```text
RequirementRunCreateRequest
        ↓
AutonomousProductRuntimeService
        ↓
validated 3-task TaskDAG
        ↓ persist before dispatch
PostgreSQL Run / DAG
        ↓
DurableDramatiqTaskDispatcher
        ↓
root-a + root-b only
        ↓
real registered generation worktrees + one-parent task commits
        ↓
WORKER_EXECUTION + lease/run_token authority
        ↓
DurableMultiAgentRunController
        ↓
real topological Git integration
        ↓
accepted MergeQueueSnapshot
        ↓
RepairAwareEvidenceBoundTaskExecutionBaseResolver
        ↓
DAGRunReconciler → IdempotentTaskReconciler
        ↓
dependent only
        ↓
dependent commit from accepted integration head
        ↓
final integration
        ↓
RepairAwarePostgresMultiTaskCompletionStore
        ↓
persisted SUCCEEDED Run
        ↓
Integration Diff reconstruction
        ↓
PostgresGitHubPublicationStore
        ↓
Draft PR publication projection
```

Assertions include:

- only the two dependency-free roots are dispatched at launch;
- the browser supplies no task contracts, dependency edges, Git bases, dispatch IDs, lease state or tokens;
- both roots use the frozen Run base and remain isolated in registered Git worktrees;
- after accepted root integration, exactly one dependent dispatch is prepared/published;
- the dependent worker commit has the accepted integration head as its sole parent;
- final integration contains all three tasks and has exact evidence-reproducible Git parents;
- terminal Run state is persisted as `SUCCEEDED` by the repair-aware completion store;
- the dependent Integration Diff is reconstructed from accepted merge evidence and Git objects;
- GitHub publication source basis is `INTEGRATION` and points to the final integration commit;
- Draft PR publication is persisted without changing terminal Run truth.

---

## Step 6.6 crash-durability prerequisite

Before Step 6.7 acceptance, implementation head `6673d87a3aea15a68196a15818a109e74046d1d7` independently passed strict CI after adding repair staging refs.

The recovery test simulates a process failure after `INTEGRATION_REPAIR` persistence but before integration-ref CAS, then executes:

```text
git reflog expire --expire=now --all
git gc --prune=now
```

The server-owned `refs/devflow/integration-repairs/<run>/<conflict-marker>` anchor keeps the repair object alive. A fresh service instance reuses the exact persisted repair evidence, performs no second Agent call, completes the Git CAS, releases the staging ref, and reconstructs `INTEGRATED → CONFLICT → REPAIRED` with `stopped=false`.

The staging ref is object-liveness protection only. It is never accepted as replacement for PostgreSQL typed repair authority.

---

## Implementation-head CI evidence

Pull Request: **#38 — Phase 6: Autonomous Multi-Agent Product Loop**

Branch implementation head:

`e4f12f2fe0f90b2d789f242f22d4b7b3a9126108`

Backend Quality run **#903**:

- PostgreSQL + Redis services: **PASS**;
- Alembic upgrade → downgrade base → re-upgrade: **PASS**;
- verification Docker image build: **PASS**;
- Ruff: **PASS**;
- V1 benchmark fixture validation: **PASS**;
- deterministic V1 control-plane demos: **5 / 5 PASS**;
- pytest: **402 passed in 38.03s**.

Frontend Quality run **#198** on the same implementation head:

- locked install: **PASS**;
- TypeScript typecheck: **PASS**;
- lint: **PASS**;
- Vitest: **PASS**;
- Vite production build: **PASS**.

The final documentation / progress ledger head must independently pass both quality workflows before PR acceptance is considered fully closed.

---

## Frozen Step 6.7 boundary

> **The E2E may replace external nondeterminism with deterministic fakes, but it may not replace Git, PostgreSQL evidence, durable dispatch/reconciliation, completion validation, Diff reconstruction, or publication-source selection with test-owned success claims.**

With this acceptance, Steps 6.1 through 6.7 form one coherent Autonomous Multi-Agent Product Loop. Remaining Phase 5 causal tracing, broader operator recovery/approval tooling, and full chaos/recovery benchmarking remain independently tracked backlog work and are not implied complete by Phase 6.