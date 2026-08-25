# Release Hardening V2 Acceptance

Status: **ACCEPTED / COMPLETE**, subject only to this accepted-state head independently passing the same four strict workflows before merge.

## Scope

Release Hardening V2 closes the real-world clone-to-run gaps without weakening DevFlow authority boundaries.

Accepted increments:

- V2.1 — production model configuration and operational readiness
- V2.2 — durable Project lifecycle, branch-scoped identity, private-read provisioning and fetch-only sync
- V2.3 — frozen-commit repository-aware planning context
- V2.4 — first project-aware Python/Node verification profiles with fail-closed environment classification
- V2.5 — real distributed release E2E over HTTP -> PostgreSQL -> Redis -> Dramatiq -> OpenAI-compatible provider -> verifier -> Git integration -> evidence
- V2.6 — minimal portable distribution: Docker Compose infrastructure, persistent PostgreSQL volume, Windows helper/CI, pinned Python 3.11 + Node 24 support, locked backend environment, and Python/Node verifier release build

## V2.6 implementation evidence

Implementation head:

`03a46e308fd440f9728116c12701003d20415d1d`

Strict workflows:

- Backend Quality #1021 (`32821187588`) — PASS
  - Alembic `base -> 0008 -> base -> 0008` PASS
  - Ruff PASS
  - deterministic V1 demos **5/5 PASS**
  - deterministic V1.1 chaos **10/10 PASS**, 7 invariants
  - pytest **458 passed, 1 existing deprecation warning**
- Frontend Quality #336 (`32821187537`) — PASS
- Release Readiness #61 (`32821187546`) — PASS
  - locked backend install PASS
  - `compose.dev.yml` validation PASS
  - Python + Node trusted verification bases PASS
  - API + Dramatiq + frontend startup PASS
  - V2.5 real distributed release E2E PASS
  - deterministic autonomous product E2E PASS
- Windows Portability #1 (`32821187640`) — PASS
  - locked Python 3.11 environment PASS
  - portable backend/Git checks PASS
  - Node 24 frontend typecheck/build PASS
  - PowerShell helper syntax PASS

## Frozen release boundary

> Portability, readiness, Compose, dependency locks and platform checks improve delivery confidence; they do not become runtime success authority.

PostgreSQL typed evidence, deterministic verification, Git provenance, lease/generation/`run_token` fencing, Human Gate and accepted integration history remain authoritative.

## Deliberate backlog

The following are explicitly outside the accepted résumé/demo scope rather than hidden release blockers:

- broader Python/Node matrices
- generalized `uv.lock` / arbitrary pyproject verification environments
- cursor pagination for very large Run/Project histories
- generalized submodule support and very-large-repository optimization

No runtime feature expansion is required for Release Hardening V2 acceptance.
