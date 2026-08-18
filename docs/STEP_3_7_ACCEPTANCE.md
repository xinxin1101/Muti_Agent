# Step 3.7 Acceptance — `run_token` Stale-Write Protection

Status: **ACCEPTED / COMPLETE**

Step 3.7 is accepted with the following frozen guarantees and boundaries.

- every initial task acquisition receives generation `1` and a fresh `run_token`;
- EXPIRED ownership may be atomically replaced by a new owner with a strictly later generation and a
  different token;
- EXPIRED takeover requires a fresh `dispatch_id`, preventing a new generation from silently reusing
  the previous generation's dispatch-scoped evidence namespace;
- ACTIVE ownership remains exclusive;
- RELEASED ownership remains non-reacquirable terminal history;
- token identity is not conflated with human-readable `worker_id` or Redis `dispatch_id`;
- `run_token` never enters `TaskDispatchEnvelope`, Redis messages, ordinary `TaskLeaseSnapshot`
  serialization, routine logs, or branch names;
- PostgreSQL database time remains authoritative for lease liveness;
- an expired generation loses task-scoped worker write authority before takeover, not only after it;
- an unowned task rejects a caller-supplied fabricated token instead of falling back to the Step 3.4
  legacy non-fenced path;
- the Step 3.4 local/non-leased persistence path remains valid only when neither persisted task state
  nor caller supplies a token;
- late heartbeat from the old token fails closed after takeover;
- late PostgreSQL evidence append from the old token fails closed;
- late single-task finalization from the old token fails closed;
- current-token evidence writes remain accepted and idempotent under the Step 3.4 evidence-key rules;
- heartbeat and release require the current token;
- both generation worktree/branch creation and final task commit/ref publication require the current
  live token and dispatch;
- token validation and each runtime-owned Git mutation are serialized against ownership transfer so
  takeover cannot occur in the check/use gap;
- generation-scoped worktree/branch identity allows a replacement worker to run even when the abandoned
  generation left its old worktree in place;
- a real Git regression proves a stale generation cannot create a new branch ref after takeover while
  the takeover generation can create its workspace and publish successfully;
- migration `0003_run_token_fencing` upgrades existing Step 3.6 ownership into a fenced generation and
  remains downgrade/upgrade reversible in CI;
- real PostgreSQL concurrency/expiry/takeover tests pass;
- Redis/Dramatiq integration proves the token is created after delivery and propagated only inside the
  worker process;
- Docker verifier, Ruff, and the complete previous regression suite remain green;
- no exactly-once claim is introduced;
- no automatic worker-death redispatch/recovery controller is introduced;
- no Step 3.8 structured logging, frontend/SSE, vector retrieval, Agent/AST/RAG expansion, scheduler
  rewrite, or generic LLM merge repair is introduced.

## Acceptance history

- The first candidate passed PostgreSQL migration and Docker verification, then Ruff stopped the gate
  on five static-only issues. Those were repaired without weakening fencing semantics.
- The next candidate passed migration, Docker, Ruff and **271 tests**.
- Merge review then hardened three boundaries: fabricated tokens can no longer fall back to the legacy
  non-fenced path; EXPIRED takeover requires a fresh dispatch namespace; and fencing was expanded from
  final Git publication to all runtime-owned Git ref mutations, including `git worktree add -b`.
- The first hardened run reached **272 / 273 tests**; its sole failure was an invalid one-event
  `SingleTaskRunResult` test fixture, while the fabricated-token rejection assertion had already passed.
- The fixture alone was repaired to use the existing schema-valid `PENDING → SUCCEEDED` event history.
- Code head `56895eb38629190ae6defd4cf6981ec124f2bd6b` then passed PostgreSQL + Redis services,
  Alembic `0001 → 0002 → 0003 → downgrade base → 0001 → 0002 → 0003`, Docker verifier, Ruff,
  and **273 passed in 30.17s, 0 skipped** under GitHub Actions `Backend Quality`.
- No paid SiliconFlow call is required by Step 3.7 acceptance tests.

Frozen acceptance statement:

> **Lease expiry revokes the old generation's write authority; a fresh token fences the replacement
> generation so only the current live owner can mutate DevFlow worker state.**
