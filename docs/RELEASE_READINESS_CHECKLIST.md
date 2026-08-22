# Release Readiness Checklist

Status: **IN PROGRESS / NOT YET ACCEPTED**

- [ ] Repository-root `.env` loads independent of backend working directory
- [ ] Product Quick Start covers infrastructure, migrations, verifier, API, worker and frontend
- [ ] API fails fast on an unmigrated database with an actionable Alembic instruction
- [ ] `.env.example` documents GitHub Draft PR publication settings
- [ ] Root README reflects accepted Phase 5/V1.1 and Phase 6 state
- [ ] Backend README reflects current lease/fencing/recovery semantics
- [ ] Frontend/API localhost defaults are consistent
- [ ] Release smoke exercises the public startup/product path deterministically
- [ ] Backend Quality passes on release-readiness head
- [ ] Frontend Quality passes on release-readiness head
- [ ] V1 control-plane demos remain 5/5
- [ ] V1.1 chaos matrix remains 10/10
- [ ] Release smoke passes

No merge to `main` is authorized until every release blocker above is complete.
