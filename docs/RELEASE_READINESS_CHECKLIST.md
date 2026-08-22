# Release Readiness Checklist

Status: **ACCEPTED / COMPLETE**

- [x] Repository-root `.env` loads independent of backend working directory
- [x] Relative managed workspace root resolves from repository root
- [x] Alembic uses the same repository-root Settings source
- [x] Blank provider/publication secrets are normalized to not configured
- [x] Product Quick Start covers infrastructure, migrations, verifier, API, worker and frontend
- [x] API fails fast on an unmigrated/stale database with an actionable Alembic instruction
- [x] `.env.example` documents GitHub Draft PR publication settings
- [x] Root README reflects accepted Phase 5/V1.1 and Phase 6 state
- [x] Backend README reflects current lease/fencing/recovery semantics
- [x] Frontend/API/Vite loopback defaults are consistent
- [x] Release smoke exercises real API/worker/frontend startup plus deterministic Product E2E
- [x] Backend Quality passes on release-readiness implementation head
- [x] Frontend Quality passes on release-readiness implementation head
- [x] V1 control-plane demos remain 5/5
- [x] V1.1 chaos matrix remains 10/10
- [x] Release Readiness smoke passes

Implementation acceptance evidence:

- head `71be46474a6d75aac9d692b15f105dfd8b8e6017`
- Backend Quality #968: PASS — 446 tests, 5/5 demos, 10/10 chaos
- Frontend Quality #283: PASS
- Release Readiness #12: PASS

The accepted-state head created by this ledger transition must pass the same three workflows before sequential promotion to `main`.
