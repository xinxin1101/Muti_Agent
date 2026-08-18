# Step 3.6 Acceptance — Lease + Heartbeat

Status: **CANDIDATE / NOT YET ACCEPTED**

Step 3.6 is accepted only when the branch demonstrates all of the following without introducing
Step 3.7 fencing semantics:

- per-task PostgreSQL lease ownership keyed by `(run_id, task_id)`;
- PostgreSQL database time is authoritative for acquisition, heartbeat, expiry and release;
- ACTIVE leases are exclusive and require exact worker/dispatch identity for renewal/release;
- two independent PostgreSQL clients racing to acquire one task produce exactly one ACTIVE owner and
  one fail-closed conflict;
- heartbeat renewal extends `heartbeat_at` and `lease_until`;
- terminal Runs reject new lease acquisition, while the exact already-established live owner may
  heartbeat/release during deterministic terminal unwind;
- normal completion releases a live lease;
- missing heartbeat yields inspectable `EXPIRED` / abandoned evidence;
- EXPIRED leases cannot be resurrected or reassigned in Step 3.6;
- RELEASED lease history is retained and is not silently reused;
- production Dramatiq execution is wrapped by lease acquisition/heartbeat/release;
- heartbeat failure cooperatively cancels inner execution but makes no stale-write-fencing claim;
- a dedicated regression proves stale PostgreSQL evidence writes are still technically possible
  after lease expiry, explicitly preserving the Step 3.7 boundary;
- Alembic `upgrade head → downgrade base → upgrade head` passes with migration 0002;
- real PostgreSQL lease tests pass;
- existing Redis/Dramatiq integration remains green;
- Docker verifier build, Ruff and all previous tests remain green;
- no `run_token`, stale-worker fencing, frontend, vector retrieval, or Agent/AST/RAG expansion is
  introduced.

Frozen acceptance statement:

> **Step 3.6 may identify live versus abandoned ownership. Safe ownership transfer remains blocked
> until Step 3.7 can fence stale writers.**
