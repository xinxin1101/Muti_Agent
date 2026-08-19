# Step 4.7 Acceptance — GitHub Branch + Draft PR Integration

Status: **ACCEPTED / COMPLETE**

Step 4.7 is accepted with the following frozen guarantees and boundaries:

- only an already-SUCCEEDED persisted Run is publication-eligible;
- multi-task publication requires complete accepted integration evidence covering every persisted task;
- single-task fallback uses only unique accepted successful worker commit evidence whose base equals the persisted Run base;
- source commits and accepted parent chains are revalidated from the managed local Git repository before publication;
- browser requests cannot supply commit SHA, ref, branch, local path, base/head SHA, PR title/body, or credentials;
- the remote branch is deterministically restricted to `devflow/run-<run_id>`;
- an existing exact branch is idempotent while the same branch at a different SHA fails closed;
- publication never force-pushes, deletes refs, or writes the default branch;
- GitHub credentials stay backend-only and never appear in command arguments, browser DTOs, generic evidence, runtime events, persisted audit intent/claim data, or bounded external errors;
- REST credentials are pinned to `https://api.github.com`, with explicit API version `2026-03-10` and typed validation of returned PR identity;
- only an exact open Draft PR matching repository/head SHA/head branch/base branch and exact PR URL/number identity is accepted;
- GitHub duplicate behavior is not treated as the concurrency lock: one PostgreSQL PUBLISHING claim with backend-only attempt token and DB-time expiry fences concurrent publication attempts;
- a second live attempt fails closed; an expired attempt may be taken over with a new token; the stale token cannot persist FAILED or PUBLISHED afterward;
- configured production Git/GitHub operation timeout remains bounded within the publication claim window;
- retries cannot create uncontrolled duplicate branches or Pull Requests;
- terminal typed evidence remains append-closed; publication results use the separate non-authoritative `github_publications` audit projection;
- publication intent is immutable/hash-validated and audit corruption fails closed;
- an already-PUBLISHED audit row cannot be downgraded by a late failure;
- GitHub failure never rewrites accepted local Git/runtime truth or Run status;
- `GET /api/v1/runs/{run_id}/github-publication` is selector-free;
- `POST /api/v1/runs/{run_id}/github-publication` accepts no query selectors and no request body;
- React exposes only backend-selected publication facts plus a no-body Draft PR action for eligible Runs;
- React treats PUBLISHING as a read-only projection: retries ask the backend to decide live-claim conflict versus DB-time takeover, and no claim token is exposed;
- publication success updates only publication state and never derives or mutates Run success;
- Step 4.8 benchmark/demo behavior remains deferred.

## Final exact-head implementation acceptance

Accepted implementation head before ledger advancement:

`2cb2544c06c6a5f48e29ac4f6ccb91678de53c37`

Backend Quality on that exact head:

- PostgreSQL + Redis service startup: **PASS**;
- Alembic `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → downgrade base → upgrade through 0006`: **PASS**;
- verification Docker image build: **PASS**;
- `ruff check .`: **PASS**;
- persisted SUCCEEDED eligibility and single-task Run-base provenance regressions: **PASS**;
- real two-task integration-source / exact Git parent-chain regressions: **PASS**;
- non-force/default-branch/ref-deletion Git publication regressions: **PASS**;
- branch same-SHA idempotence / different-SHA conflict regressions: **PASS**;
- PostgreSQL PUBLISHING claim, takeover, stale-token fencing, and PUBLISHED non-downgrade regressions: **PASS**;
- selector/body-free API regressions: **PASS**;
- GitHub REST version/header/open-Draft/head/base/URL identity regressions: **PASS**;
- GitHub API credential-destination pinning regression: **PASS**;
- complete backend `pytest`: **332 passed in 32.79s** with no pytest warning summary.

Frontend Quality on the same exact head:

- locked `npm ci`: **PASS**;
- strict TypeScript typecheck: **PASS**;
- lint: **0 warnings / 0 errors**;
- Vitest: **25 passed across 6 test files**;
- non-SUCCEEDED Runs expose no publication query/action: **PASS**;
- backend-selected publication facts only / no browser SHA-ref-token form: **PASS**;
- PUBLISHING stale-claim recovery remains backend-owned: **PASS**;
- publication success does not mutate Run status/query authority: **PASS**;
- Vite production build: **PASS**.

Step 4.7 CI does not perform a live external GitHub mutation: GitHub REST behavior is exercised through typed mock transports and Git command boundaries through deterministic/fake or temporary local repositories. No paid SiliconFlow API call is required.

Merge review confirmed no unresolved review threads and no path from GitHub branch/PR/check state to Run success, verification, Reviewer approval, integration authorization, Human Gate, lease ownership, or `run_token` authority.

Both `docs/GITHUB_PUBLICATION.md`, this acceptance document, and `docs/PROGRESS.md` are covered by Backend Quality and Frontend Quality path gates.

## Acceptance ledger cleanup

The one-time Step 4.7 ledger-bootstrap workflow used only to perform bounded in-repository text replacement was removed before final acceptance in commit `d1ca5f51ef835262260342872fe8ac605a148122`. The final merge head therefore retains the normal read-only Backend/Frontend quality workflows and no temporary `contents: write` bootstrap workflow. This acceptance update intentionally re-triggers both quality workflows after that cleanup so the final ledger head itself is validated before merge.

Frozen Step 4.7 principle:

> **Accepted runtime/Git evidence selects what may be published; GitHub only receives that already-accepted projection.**
