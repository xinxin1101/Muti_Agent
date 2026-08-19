# Step 4.1 Acceptance — React / TypeScript UI Foundation

Status: **ACCEPTED / COMPLETE**

Step 4.1 is accepted with the following frozen guarantees and boundaries.

- the frontend is isolated under `frontend/`;
- React + TypeScript + Vite production build passes;
- TypeScript strict typecheck passes;
- Oxlint passes;
- Vitest + Testing Library regressions pass;
- a committed npm lockfile makes dependency resolution reproducible;
- CI installs dependencies with `npm ci` under Node.js 24;
- the minimal App Shell provides Foundation / Projects / Runs navigation;
- Projects and Runs remain explicit Step 4.2 placeholders;
- TanStack Query is present only as a server-state client boundary;
- Tailwind CSS is integrated through the Vite plugin;
- browser configuration exposes only a public API base URL;
- browser runtime-event DTOs align with the accepted backend `PersistedRuntimeEvent` wire shape;
- browser runtime-event DTOs contain no `run_token` or credentials;
- no backend scheduler, persistence, fencing, verification, or event authority is changed;
- no SSE/WebSocket, DAG visualization, diff viewer, metrics, or GitHub publication is introduced.

## Acceptance history

- Initial PR head established the React/TypeScript/Vite scaffold and passed typecheck, lint, tests, and build.
- Merge review aligned browser event DTOs with the accepted snake_case backend event model and added a reproducible dependency lockfile.
- A temporary same-repository CI bootstrap generated and committed `frontend/package-lock.json`; that write permission is not part of the accepted final workflow.
- Code head `f36f54ece2005c4e8b19613cf869856a3599ad67` restored `contents: read`, switched installation to `npm ci`, and passed the complete `Frontend Quality` gate.
- The acceptance-ledger update is documentation-only and is deliberately included in the workflow path gate so the final PR head is revalidated before merge.

Frozen Step 4.1 principle:

> **The browser may present runtime truth; it may not manufacture runtime truth.**
