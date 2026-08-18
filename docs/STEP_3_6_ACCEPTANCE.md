# Step 3.6 Acceptance — Lease + Heartbeat

Status: **ACCEPTED**

Step 3.6 is accepted because the implementation demonstrates all of the following without
introducing Step 3.7 fencing semantics:

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
- fallback worker identity is stable per actual process id and changes after a fork/process change;
- heartbeat failure cooperatively cancels inner execution but makes no stale-write-fencing claim;
- a dedicated regression proves stale PostgreSQL evidence writes are still technically possible
  after lease expiry, explicitly preserving the Step 3.7 boundary;
- Alembic `upgrade head → downgrade base → upgrade head` passes with migration 0002;
- real PostgreSQL lease tests pass;
- existing Redis/Dramatiq integration remains green;
- Docker verifier build, Ruff and all previous tests remain green;
- no `run_token`, stale-worker fencing, frontend, vector retrieval, or Agent/AST/RAG expansion is
  introduced.

## Acceptance history

### First candidate

The first candidate successfully initialized PostgreSQL and Redis, completed the full Alembic
migration cycle, and built the Docker verification image. Ruff then stopped the gate on 11 static
issues: return-self typing, one unused import, line-length formatting and modern UTC spelling. These
were repaired without changing lease semantics.

### Second candidate

After the static-only repair:

- migration cycle: **PASS**;
- Docker verifier: **PASS**;
- Ruff: **PASS**;
- pytest: **268 passed in 30.10s, 0 skipped**;
- Backend Quality: **SUCCESS**.

Merge review then identified a terminal-cleanup race: for a single-task persisted Run, the inner
`QueuedTaskWorker` can finalize the Run immediately before the outer lease wrapper stops heartbeat
and releases ownership. A heartbeat in that window must not be rejected merely because the Run is
already terminal. The accepted rule is therefore:

- terminal Run → no new lease acquisition;
- exact already-established live owner → heartbeat/release allowed during deterministic unwind;
- expired/released lease → still cannot renew.

The same review added a real PostgreSQL race test using two independent lease stores. Exactly one
client acquires the task and the other fails closed after database serialization.

### Final ownership hardening

The generated default `worker_id` was changed from an import-time value to a PID-keyed cached value.
This preserves one stable label inside a worker process while ensuring a forked/different process
receives a distinct fallback identity. Explicit `DEVFLOW_WORKER_ID` remains unchanged.

Final CI for branch head `27e006bed3005f70dbd753d00c6f51ffe1880203`:

- PostgreSQL service: **PASS**;
- Redis service: **PASS**;
- Alembic `0001 → 0002 → downgrade base → 0001 → 0002`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- real PostgreSQL concurrent lease acquisition: **PASS**;
- terminal-run lease unwind regression: **PASS**;
- existing Redis/Dramatiq worker integration: **PASS**;
- stale-write-still-possible Step 3.7 boundary regression: **PASS**;
- `pytest`: **270 passed in 30.71s, 0 skipped**;
- GitHub Actions `Backend Quality`: **SUCCESS**.

No paid SiliconFlow call is required by the lease/heartbeat acceptance tests.

## Frozen boundary

Step 3.6 may identify live versus abandoned ownership, but it deliberately cannot guarantee that an
expired old worker has lost write capability. `EXPIRED` is therefore not a takeover authorization in
this step, and no new worker is allowed to acquire an expired or released task lease.

Frozen acceptance statement:

> **Step 3.6 may identify live versus abandoned ownership. Safe ownership transfer remains blocked
> until Step 3.7 can fence stale writers.**
