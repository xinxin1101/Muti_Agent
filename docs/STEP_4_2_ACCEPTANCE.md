# Step 4.2 Acceptance — Product Pages

Status: **ACCEPTED / COMPLETE**

Step 4.2 is accepted with the following frozen guarantees and boundaries.

- FastAPI product endpoints expose bounded typed DTOs rather than raw persistence rows;
- Project creation accepts only absolute HTTPS repository URLs without embedded credentials;
- managed Project workspaces remain backend-owned under the configured workspace root;
- existing managed workspaces must match both the persisted repository origin and default branch;
- project-level symbolic-link workspaces fail closed;
- New Run accepts a validated `TaskContract` and does not accept browser-supplied `base_commit`;
- the backend derives the exact Run `base_commit` from the managed Git workspace HEAD;
- a missing or untrustworthy managed workspace returns an explicit conflict rather than creating a Run;
- Run persistence uses the accepted `PostgresEvidenceStore`;
- execution delivery uses the accepted `DramatiqTaskDispatcher` and existing leased/fenced worker runtime;
- Redis broker rejection is surfaced as `BROKER_UNAVAILABLE` without manufacturing queued or successful runtime state;
- persisted Run status is validated through `PersistedRunStatus`, so unknown/corrupted status values fail closed;
- Projects / New Run / Runs / Run Dashboard / Task Detail pages consume typed backend contracts;
- Task Detail exposes bounded TaskContract and evidence metadata/hash identity, not raw evidence payloads;
- frontend wire DTOs remain snake_case-aligned with backend contracts;
- browser configuration contains no Git/provider/database/Redis credential or `run_token`;
- Backend Quality and Frontend Quality both gate Step 4.2 product code and acceptance/progress documentation;
- no SSE/WebSocket, DAG visualization, diff viewer, run metrics, or GitHub publication is introduced.

## Acceptance history

- Initial implementation head `788a24b99af2b783aedd0d3fa216f4f9bea68eda` established the FastAPI/browser product boundary and passed both quality workflows; Backend Quality completed with **290 passed**. Merge review retained the candidate instead of merging immediately because the API test path still emitted a Starlette/TestClient deprecation warning and several fail-closed boundaries were only implicit.
- Hardening head `8b81f1cd6558e5f766192d07bc8166aa25d9cf92` added persisted-status enum validation, explicit workspace-not-ready conflict handling, managed-project symlink rejection, default-branch consistency checking, and ASGITransport-based API tests. Its first Backend Quality attempt stopped only on a mechanical Ruff import-order finding before pytest.
- Mechanical lint-fix head `d427478a779e34c6d88bc444efed8d52b74cb543` passed PostgreSQL migrations, Docker verifier build, Ruff, and **293 passed in 33.82s**. The earlier TestClient deprecation warning was absent. Frontend Quality also passed locked install, strict typecheck, lint, UI/API tests, and production build.
- Workflow-gate head `2209134d49ca87fadc590a0f9e1eb938bcaa897a` added `docs/PRODUCT_PAGES.md`, `docs/STEP_4_2_ACCEPTANCE.md`, and `docs/PROGRESS.md` to the relevant Backend/Frontend Quality path filters while retaining read-only repository permissions. Both quality workflows passed again.
- The final acceptance/progress ledger update is intentionally covered by those path gates so the exact PR head must re-run both Backend Quality and Frontend Quality before merge.

Frozen Step 4.2 principle:

> **The browser may request work; Git, typed persistence, and the accepted runtime still decide what work exists and what happened.**
