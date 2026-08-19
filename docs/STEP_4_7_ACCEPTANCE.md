# Step 4.7 Acceptance — GitHub Branch + Draft PR Integration

Status: **CANDIDATE / PENDING CI**

Step 4.7 is ready for acceptance only when the exact PR head proves:

- only an already-SUCCEEDED persisted Run is publication-eligible;
- multi-task publication requires complete accepted integration evidence covering every persisted task;
- single-task fallback uses only unique accepted successful worker commit evidence;
- source commits and accepted parent chains are revalidated from the managed local Git repository before publication;
- browser requests cannot supply commit SHA, ref, branch, local path, base/head SHA, PR title/body, or credentials;
- the remote branch is deterministically restricted to `devflow/run-<run_id>`;
- an existing exact branch is idempotent while the same branch at a different SHA fails closed;
- publication never force-pushes, deletes refs, or writes the default branch;
- GitHub credentials stay backend-only and never appear in command arguments, browser DTOs, generic evidence, runtime events, persisted audit intent, or bounded external errors;
- the GitHub REST boundary uses an explicit API version and typed validation of returned PR identity;
- only an exact Draft PR matching repository/head SHA/head branch/base branch is accepted;
- retries cannot create uncontrolled duplicate branches or Pull Requests;
- terminal typed evidence remains append-closed; publication results use the separate non-authoritative `github_publications` audit projection;
- publication intent is immutable/hash-validated and audit corruption fails closed;
- an already-PUBLISHED audit row cannot be downgraded by a late failure;
- GitHub failure never rewrites accepted local Git/runtime truth or Run status;
- `GET /api/v1/runs/{run_id}/github-publication` is read-only and selector-free;
- `POST /api/v1/runs/{run_id}/github-publication` accepts no query selectors and no request body;
- React exposes only backend-selected publication facts plus a no-body Draft PR action for eligible Runs;
- publication success updates only publication state and never derives or mutates Run success;
- Backend Quality and Frontend Quality remain green on the same exact head;
- Step 4.8 benchmark/demo behavior remains deferred.

Frozen candidate principle:

> **Accepted runtime/Git evidence selects what may be published; GitHub only receives that already-accepted projection.**
