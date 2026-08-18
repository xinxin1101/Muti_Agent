# Step 3.8 Acceptance — Structured Run/Event Logs

Status: **CANDIDATE / NOT YET ACCEPTED**

Step 3.8 is accepted only when the branch proves all of the following without expanding into frontend
streaming or the next roadmap stage.

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
- `run_token` never appears as an event field, attribute key, branch-name derivative, or serialized
  event payload;
- structured sensitive attributes are rejected before persistence;
- event attributes are bounded and hash-validated on read;
- corrupted attributes fail closed as persistence corruption;
- event queries support bounded filtering by task, dispatch, source, kind and `after_sequence`;
- same-Run concurrent accepted facts produce unique monotonic event sequences;
- terminal Run cleanup can still record lease release without reopening typed evidence writes;
- migration `0004_structured_runtime_events` upgrades/downgrades cleanly after migrations 0001–0003;
- PostgreSQL + Redis/Dramatiq + Docker verifier + Ruff + complete regression suite remain green;
- no paid SiliconFlow API call is required by Step 3.8 acceptance tests;
- no frontend/SSE/WebSocket, external log collector, scheduler rewrite, automatic worker-death
  redispatch, Agent/RAG/AST expansion, or exactly-once claim is introduced.

Frozen acceptance statement:

> **Typed evidence decides what happened; a monotonic structured event projection makes the accepted
> Run / Task / Dispatch / Generation history queryable without becoming a second success authority.**
