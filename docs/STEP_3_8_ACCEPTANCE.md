# Step 3.8 Acceptance — Structured Run/Event Logs

Status: **ACCEPTED / COMPLETE**

Step 3.8 is accepted with the following frozen guarantees and boundaries.

- a new persisted Run receives a structured `RUN_STARTED` event;
- events receive a strictly increasing per-Run sequence assigned under the PostgreSQL Run lock;
- `(run_id, event_key)` is an idempotency boundary and conflicting reuse fails closed;
- newly accepted typed evidence creates an `EVIDENCE_RECORDED` projection in the same transaction;
- duplicate/idempotent evidence retries do not create duplicate event projections;
- Developer / Verification / Reviewer / Repair / Dispatch / Worker / Integration evidence can be
  distinguished through structured event `source` + evidence-kind metadata;
- single-task terminal persistence creates `RUN_FINALIZED` in the same transaction as terminal result;
- lease acquisition, takeover, heartbeat and release produce generation-correlated events;
- EXPIRED → takeover produces a later generation in the event timeline;
- generated runtime events never include the current `run_token`; structured sensitive attribute keys
  are rejected before persistence;
- event attributes are bounded to 16 KiB of canonical JSON and hash-validated on read;
- corrupted attributes fail closed as persistence corruption;
- event queries support bounded filtering by task, dispatch, source, kind and `after_sequence`;
- same-Run concurrent accepted facts produce unique monotonic event sequences;
- terminal Run cleanup can still record lease release without reopening typed evidence writes;
- migration `0004_structured_runtime_events` upgrades/downgrades cleanly after migrations 0001–0003;
- PostgreSQL + Redis/Dramatiq + Docker verifier + Ruff + complete regression suite remain green;
- no paid SiliconFlow API call is required by Step 3.8 acceptance tests;
- no frontend/SSE/WebSocket, external log collector, scheduler rewrite, automatic worker-death
  redispatch, Agent/RAG/AST expansion, or exactly-once claim is introduced.

## Acceptance history

- The first candidate passed PostgreSQL migration and Docker verification; Ruff stopped the gate on one
  formatting-only line-length issue. The fix was mechanical and did not alter event semantics.
- The next candidate passed the full quality gate with **278 tests**.
- Merge review then converted two documented contracts into explicit regressions: the 16 KiB event
  attribute bound and actual persistence projection from Developer / Verification / Reviewer / Repair /
  Dispatch / Worker evidence into distinct structured `source` values.
- That hardened candidate passed the full quality gate with **280 tests**.
- Final merge review added the Step 3.7/3.8 terminal-unwind boundary: after `RUN_FINALIZED`, typed
  evidence remains append-closed while the exact current live lease owner may still persist the outer
  `LEASE_RELEASED` cleanup event.
- Code head `e92252279f256019130b4b416714fedac9370062` passed PostgreSQL + Redis services, Alembic
  `0001 → 0002 → 0003 → 0004 → downgrade base → 0001 → 0002 → 0003 → 0004`, Docker verifier,
  Ruff, and **281 passed in 32.69s** under GitHub Actions `Backend Quality`.
- PR scope remained limited to the structured-event migration/model/store, existing persistence/lease
  projections, dedicated regressions, and Step 3.8 documentation; no review threads or submitted review
  blockers remained before acceptance.
- No paid SiliconFlow API call is required by Step 3.8 acceptance tests.

Frozen acceptance statement:

> **Typed evidence decides what happened; a monotonic structured event projection makes the accepted
> Run / Task / Dispatch / Generation history queryable without becoming a second success authority.**
