# Step 3.7 Acceptance — `run_token` Stale-Write Protection

Status: **CANDIDATE / NOT YET ACCEPTED**

Step 3.7 is accepted only when the branch demonstrates the following without expanding into Step 3.8.

- every initial task acquisition receives generation `1` and a fresh `run_token`;
- EXPIRED ownership may be atomically replaced by a new owner with a strictly later generation and a
  different token;
- ACTIVE ownership remains exclusive;
- RELEASED ownership remains non-reacquirable terminal history;
- token identity is not conflated with human-readable `worker_id` or Redis `dispatch_id`;
- `run_token` never enters `TaskDispatchEnvelope`, Redis messages, ordinary `TaskLeaseSnapshot`
  serialization, or routine logs;
- PostgreSQL database time remains authoritative for lease liveness;
- an expired generation loses task-scoped worker write authority before takeover, not only after it;
- late heartbeat from the old token fails closed after takeover;
- late PostgreSQL evidence append from the old token fails closed;
- late single-task finalization from the old token fails closed;
- current-token evidence writes remain accepted and idempotent under the Step 3.4 evidence-key rules;
- heartbeat and release require the current token;
- Git task-branch publication requires the current live token and dispatch;
- token validation and Git ref publication are serialized against ownership transfer so takeover
  cannot occur in the check/use gap;
- generation-scoped worktree/branch identity allows a replacement worker to run even when the abandoned
  generation left its old worktree in place;
- a real Git regression proves stale generation output is not published while the takeover generation
  can publish successfully;
- migration `0003_run_token_fencing` upgrades existing Step 3.6 ownership into a fenced generation and
  remains downgrade/upgrade reversible in CI;
- real PostgreSQL concurrency/expiry/takeover tests pass;
- Redis/Dramatiq integration proves the token is created after delivery and propagated only inside the
  worker process;
- Docker verifier, Ruff, and the complete previous regression suite remain green;
- no exactly-once claim is introduced;
- no Step 3.8 structured logging, frontend/SSE, vector retrieval, Agent/AST/RAG expansion, scheduler
  rewrite, or generic LLM merge repair is introduced.

Frozen acceptance statement:

> **Lease expiry revokes the old generation's write authority; a fresh token fences the replacement
> generation so only the current live owner can publish DevFlow worker state.**
