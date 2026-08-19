# Step 4.2 Acceptance — Product Pages

Status: **CANDIDATE / PENDING CI**

Step 4.2 is ready for acceptance when the exact PR head proves:

- FastAPI product endpoints expose bounded typed DTOs;
- Project creation only accepts HTTPS repository URLs without embedded credentials;
- managed Project workspaces remain backend-owned;
- New Run accepts a validated `TaskContract`;
- browser requests cannot choose `base_commit`;
- the backend derives `base_commit` from the managed Git workspace HEAD;
- Run persistence uses the accepted `PostgresEvidenceStore`;
- execution dispatch uses the accepted `DramatiqTaskDispatcher`;
- broker rejection is surfaced without manufacturing queued/success state;
- Projects / New Run / Runs / Run Dashboard / Task Detail pages render real API data;
- Task Detail exposes bounded evidence metadata, not raw evidence payloads;
- frontend wire DTOs remain snake_case-aligned with backend contracts;
- backend PostgreSQL tests remain green;
- frontend typecheck, lint, tests, and production build remain green;
- no SSE/WebSocket, DAG visualization, diff viewer, metrics, or GitHub publication is introduced.

Frozen candidate principle:

> **The browser may request work; Git, typed persistence, and the accepted runtime still decide what work exists and what happened.**
