# Step 4.1 Acceptance — React / TypeScript UI Foundation

Status: **CANDIDATE / PENDING CI**

Step 4.1 is ready for acceptance when the following gates pass on the exact PR head:

- the frontend remains isolated under `frontend/`;
- React + TypeScript + Vite production build succeeds;
- TypeScript strict typecheck succeeds;
- frontend lint succeeds;
- Vitest + Testing Library regressions succeed;
- the minimal App Shell renders Foundation / Projects / Runs navigation;
- Projects and Runs remain explicit Step 4.2 placeholders;
- TanStack Query is present only as a server-state client boundary;
- Tailwind CSS is integrated through the Vite plugin;
- browser configuration exposes only a public API base URL;
- browser-safe runtime event types contain no `run_token`;
- no backend scheduler, persistence, fencing, verification, or event authority is changed;
- no SSE/WebSocket, DAG visualization, diff viewer, metrics, or GitHub publication is introduced;
- `Frontend Quality` GitHub Actions passes.

Frozen candidate principle:

> **The browser may present runtime truth; it may not manufacture runtime truth.**
