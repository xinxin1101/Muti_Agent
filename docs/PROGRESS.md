# DevFlow Implementation Progress

This file is the execution ledger for `docs/DEVELOPMENT_PLAN.md`. Detailed CI evidence and authority analysis remain in the corresponding acceptance documents, pull requests, workflow runs and Git history.

## Current position

- Phase 1 / V0.1 — **ACCEPTED / COMPLETE**
- Phase 2 / V0.2 — **ACCEPTED / COMPLETE**
- Phase 3 / V0.3 — **ACCEPTED / COMPLETE**
- Phase 4 / V1.0 — **ACCEPTED / COMPLETE**
- Phase 5 / V1.1 Durable Agent Runtime — **ACCEPTED / COMPLETE**
- Phase 6 Autonomous Multi-Agent Product Loop — **ACCEPTED / COMPLETE**
- Release Hardening V2 — **ACCEPTED / COMPLETE**, pending only accepted-state CI before merge

Frozen project principle:

> **Agents propose; evidence decides.**

Frozen durable-runtime principle:

> **Recovery may restore execution liveness from durable facts; it may not create, rewrite, or guess runtime truth.**

Frozen product principle:

> **A user may request work in natural language; scheduling, execution, integration, repair, success, diff and publication remain server-owned evidence decisions.**

## Accepted milestone summary

| Milestone | Capability | Status |
| --- | --- | --- |
| V0.1 | Single-task evidence loop | **ACCEPTED / COMPLETE** |
| V0.2 | True multi-Agent DAG runtime | **ACCEPTED / COMPLETE** |
| V0.3 | Safety, context and reliability | **ACCEPTED / COMPLETE** |
| V1.0 | Productization | **ACCEPTED / COMPLETE** |
| V1.1 | Durable Agent Runtime | **ACCEPTED / COMPLETE** |
| Phase 6 | Autonomous Multi-Agent Product Loop | **ACCEPTED / COMPLETE** |
| Release Hardening V2 | Real-world clone-to-run and portable delivery | **ACCEPTED / COMPLETE** |

## V1.1 durable runtime

Step 5.1 through Step 5.8 remain accepted. The final V1.1 chaos suite is `benchmarks/v1_1/chaos-recovery.json`:

- suite version `0.2.0`
- suite SHA-256 `088f8b5854448344a281f7a6e953ee23faf412b7973cdd0493c9209c6f5ed7b6`
- **10 required fault domains**
- **7 frozen recovery invariants**

The accepted runtime combines PostgreSQL durable facts, dispatch ledger, lease/heartbeat/generation, `run_token` fencing, terminal evidence, DAG reconciliation, Human Gate, bounded repair, Operator recovery and Git provenance.

## Phase 6 product loop

Accepted product path:

```text
repository + natural-language requirement
        ↓
Planner proposal
        ↓
validated + persisted TaskDAG
        ↓
durable distributed execution
        ↓
deterministic verification / independent review
        ↓
topological Git integration / bounded repair
        ↓
terminal typed evidence
        ↓
DAG / Diff / Metrics / Trace / Operator surfaces
        ↓
GitHub Draft PR projection
```

Natural-language intent can start work; only validated persisted server-side facts can advance or finish it.

## Release Hardening V2

### V2.1 — Configuration / readiness — ACCEPTED

- explicit role-to-model configuration
- provider model-catalogue preflight
- `/readyz` separated from `/healthz`
- GitHub read/publication credentials separated
- no silent model fallback or secret hot reload

### V2.2 — Durable Project lifecycle — ACCEPTED

- branch-scoped canonical repository identity
- `PROVISIONING / READY / FAILED / ARCHIVED`
- private-read provisioning boundary
- fetch-only remote sync with immutable Run base SHA
- worker-side Git identity revalidation

### V2.3 — Repository-aware planning — ACCEPTED

Planner context is derived from the frozen Run commit and bounded repository evidence rather than a stale checkout or an arbitrary first-N file list.

### V2.4 — Project-aware verification Phase 1 — ACCEPTED

Python and Node verification profiles are fail-closed. Dependency preparation is separate from authoritative networkless/read-only verification.

### V2.5 — Real Distributed Release E2E — ACCEPTED

Verified production path:

```text
HTTP
  ↓
PostgreSQL
  ↓
Redis
  ↓
Dramatiq worker
  ↓
OpenAI-compatible provider
  ↓
Developer / verifier / Reviewer
  ↓
Git task commit + topological integration
  ↓
dependent-task redispatch
  ↓
terminal evidence-authorized Run success
```

Release Readiness #60 proved this path before V2.6 began.

### V2.6 — Minimal Portable Distribution — ACCEPTED

Implementation head:

`03a46e308fd440f9728116c12701003d20415d1d`

Implementation CI:

- Backend Quality #1021 (`32821187588`) — **PASS** — 5/5 demos, 10/10 chaos, **458 passed**
- Frontend Quality #336 (`32821187537`) — **PASS**
- Release Readiness #61 (`32821187546`) — **PASS**
- Windows Portability #1 (`32821187640`) — **PASS**

Accepted delivery surface:

- root `compose.dev.yml` with persistent PostgreSQL volume
- `scripts/dev.ps1` Windows helper
- Python 3.11 support frozen through `.python-version` and package metadata
- Node 24 support frozen through `.nvmrc`, package engine and `engine-strict`
- `backend/requirements-dev.lock` used by Linux and Windows quality gates
- Release CI builds both Python and Node trusted verifier bases
- README Quick Start uses the same Compose/lock/runtime contract as CI

See `docs/RELEASE_HARDENING_V2_ACCEPTANCE.md` for the acceptance snapshot.

## Deliberate backlog

The following are explicit post-V2 backlog and are not hidden blockers for the current résumé/demo scope:

- broader Python/Node runtime matrices
- generalized `uv.lock` / arbitrary pyproject verification environments
- cursor pagination for very large Run/Project histories
- generalized submodule and very-large-repository support

## Current acceptance boundary

The implementation head passed Backend, Frontend, Release and Windows gates before this ledger transition. This accepted-state head must independently pass the same four workflows. After that external proof, PR #43 may be marked Ready and merged to `main`; the merge does not alter runtime authority.
