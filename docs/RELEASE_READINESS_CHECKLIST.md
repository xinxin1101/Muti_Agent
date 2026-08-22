# Release Readiness Checklist

Status: **IMPLEMENTED / CI VALIDATION PENDING**

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
- [ ] Backend Quality passes on release-readiness head
- [ ] Frontend Quality passes on release-readiness head
- [ ] V1 control-plane demos remain 5/5
- [ ] V1.1 chaos matrix remains 10/10
- [ ] Release Readiness smoke passes

No merge to `main` is authorized until every release blocker above is complete.
