# Muti_Agent / DevFlow

Evidence-driven Multi-Agent software engineering runtime.

DevFlow turns a repository plus a natural-language requirement into a validated multi-task execution plan, runs isolated coding agents, verifies their output deterministically, integrates accepted Git commits in dependency order, pauses for durable Human decisions when required, and can publish the accepted result as a GitHub Draft Pull Request.

## Current status

**Phase 6 — Autonomous Multi-Agent Product Loop: ACCEPTED / COMPLETE on the Phase 6 implementation branch.**

Accepted product path:

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
Human Gate + bounded integration repair when required
        ↓
evidence-bound terminal Run
        ↓
DAG / SSE / Diff / Metrics
        ↓
GitHub Draft PR publication
```

Phase 6 implementation-head acceptance passed:

- PostgreSQL migration round-trip;
- Docker verifier image build;
- Ruff;
- deterministic V1 control-plane demos: **5 / 5**;
- backend pytest: **402 passed**;
- frontend locked install, typecheck, lint, Vitest and production build.

The final documentation/progress ledger remains subject to the same CI gates before PR #38 is considered fully closed. The PR remains Draft until that evidence is collected.

Phase 5 durability work is not being retroactively declared complete: causal trace correlation, the broader operator recovery/approval surface, and the full chaos/recovery benchmark remain separate backlog items.

## Core principle

> **Agents propose; evidence decides.**

LLM output may propose planning, implementation, review and bounded repair actions. It cannot establish task completion by self-report. Runtime truth comes from validated contracts, PostgreSQL typed evidence, Git parent/provenance checks, scope enforcement, deterministic verification, independent review, durable lease/`run_token` fencing, accepted integration history and explicit Human authorization where policy requires it.

## Product authority boundary

The browser may provide a repository and natural-language requirement, display accepted runtime evidence, and submit an explicit Human decision. It may not manufacture:

- TaskContracts or dependency edges for an active autonomous Run;
- Git base / task / integration SHAs;
- dispatch IDs or broker payload authority;
- lease generations or `run_token` values;
- merge success, verification success or terminal Run state;
- GitHub publication source commits.

Those values are derived or revalidated server-side from accepted durable facts.

## Project history

- **Phase 1 / V0.1** — Single Task Evidence Loop — complete.
- **Phase 2 / V0.2** — True Multi-Agent Runtime — complete.
- **Phase 3 / V0.3** — Safety, Context and Reliability — complete.
- **Phase 4 / V1.0** — Productization — complete.
- **Phase 5 / V1.1** — Durable Agent Runtime hardening — partially complete; 5.1–5.4 accepted, Human Gate/repair capabilities advanced during Phase 6, remaining tracing/operator/chaos items stay open.
- **Phase 6** — Autonomous Multi-Agent Product Loop — implementation accepted through Step 6.7.

See:

- `docs/DEVELOPMENT_PLAN.md` — original implementation plan and milestone boundaries;
- `docs/PROGRESS.md` — evidence-driven execution ledger;
- `docs/AUTONOMOUS_MULTI_AGENT_PRODUCT_LOOP.md` — Phase 6 architecture and authority boundaries;
- `docs/STEP_6_7_ACCEPTANCE.md` — Phase 6 E2E acceptance evidence;
- `docs/V1_1_ROADMAP.md` — remaining durable-runtime hardening roadmap;
- `docs/ARCHITECTURE.md` — architecture and component boundaries.
