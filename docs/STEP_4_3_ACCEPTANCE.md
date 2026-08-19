# Step 4.3 Acceptance — SSE Live Status / Logs

Status: **CANDIDATE / PENDING CI**

Step 4.3 is ready for acceptance when the exact PR head proves:

- the browser receives accepted PostgreSQL runtime events through read-only SSE;
- SSE event `id` equals the existing Run-scoped monotonic event `sequence`;
- reconnect resumes from `Last-Event-ID` / `after_sequence` without inventing another cursor;
- an unknown Run fails before stream start with HTTP 404;
- invalid resume cursors fail closed;
- event batches remain strictly monotonic and Run-scoped;
- heartbeat comments do not consume runtime sequence or create persisted facts;
- streaming reads stay bounded and use the accepted `PostgresEvidenceStore.list_runtime_events()` boundary;
- SSE serialization rejects sensitive nested attributes and bounded-payload violations;
- the browser validates event shape/enums/order before rendering;
- malformed/cross-Run/gapped event streams fail closed in the browser;
- Run status continues to come from typed REST/persistence truth rather than direct SSE client-state assignment;
- EventSource cleanup occurs on page unmount;
- backend PostgreSQL migration / Docker verifier / Ruff / pytest gates remain green;
- frontend locked install / strict typecheck / lint / tests / production build remain green;
- no DAG visualization, diff viewer, metrics, GitHub publication, or benchmark work is introduced.

Frozen candidate principle:

> **SSE may make accepted runtime history live; it may not become a second runtime history or a second success authority.**
