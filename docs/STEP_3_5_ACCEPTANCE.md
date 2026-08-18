# Step 3.5 Acceptance Notes

This file records the acceptance target while PR validation is in progress.

Step 3.5 is accepted only if Redis/Dramatiq remains a transport boundary rather than a scheduler,
repository source of truth, or execution-ownership mechanism.

Required gates:

- minimal queue payload: `dispatch_id`, `run_id`, `task_id` only;
- worker reloads persisted Run/Task evidence before execution;
- existing SingleTask runtime and Git worktree/commit gates remain authoritative;
- worker execution evidence is persisted through the Step 3.4 PostgreSQL boundary;
- Dramatiq actor automatic retries are disabled with `max_retries=0` until lease/run-token fencing;
- real Redis + Dramatiq Worker + PostgreSQL integration test passes without paid model calls;
- no lease, heartbeat, `run_token`, stale-worker fencing, frontend, vector retrieval, or Agent/AST/RAG expansion.

Final CI evidence and merge SHA are recorded in `docs/PROGRESS.md` only after acceptance.
