# Release Readiness Hardening

Status: **IN PROGRESS / NOT YET RELEASED**

This release-readiness increment fixes the public clone-to-run workflow without changing DevFlow's accepted runtime authority model.

Frozen boundary:

> Release hardening may improve configuration discovery, startup validation, documentation, local networking defaults and smoke coverage; it may not weaken PostgreSQL/Git/lease/fencing/Human/Operator authority or redefine task success.

Target release path:

```text
clone
  -> configure one repository-root .env
  -> start PostgreSQL + Redis
  -> alembic upgrade head
  -> build verifier image
  -> start API
  -> start Dramatiq worker
  -> start frontend
  -> create Project
  -> natural-language Run
  -> evidence-bound execution/recovery
  -> Diff / Trace / Metrics
  -> GitHub Draft PR publication
```

Release is blocked until Backend Quality, Frontend Quality, V1 demos, V1.1 chaos and release smoke are green on the final release-readiness head.
