# Phase 4 / Step 4.7 — GitHub Branch + Draft PR Integration

## Purpose

Step 4.7 publishes code that DevFlow has already accepted. It does not ask GitHub to decide whether a Run, task, verification, review, or integration succeeded.

Frozen principle:

> **Accepted runtime/Git evidence selects what may be published; GitHub only receives that already-accepted projection.**

## Authority chain

```text
Persisted Run status + typed Git/integration evidence
        ↓
backend publication eligibility
        ↓
local Git parent-chain revalidation
        ↓
immutable GitHubPublicationIntent
        ↓
PostgreSQL publication claim / fencing token
        ↓
DevFlow branch: devflow/run-<run_id>
        ↓
non-force bounded push
        ↓
open Draft Pull Request
        ↓
non-authoritative github_publications audit row
        ↓
React read-only publication status
```

GitHub responses never write `Run.status`, verification results, Reviewer decisions, integration gates, Human Gate decisions, leases, `run_token`, or terminal evidence.

## Eligibility

A Run must already be persisted as `SUCCEEDED` before publication can be attempted.

For multi-task Runs, publication requires a complete, non-stopped, schema-valid `MERGE_QUEUE_SNAPSHOT` whose integrated task identities exactly cover the persisted Run tasks. The final integration head is the source commit. Each task commit and integration commit is rechecked against the exact accepted parent chain in the managed local Git repository.

For a single-task Run only, when no complete integration snapshot exists, the source may fall back to the unique accepted successful `WORKER_EXECUTION.commit_sha`. Its recorded base commit must equal the persisted Run base, and the source commit must still have that accepted base as its sole parent.

No browser request may provide the source commit, branch, ref, local path, base SHA, head SHA, PR title, or PR body.

## Publication claim and concurrent retry fencing

A publication attempt is not protected only by a browser loading state or by assumed GitHub duplicate-request behavior. The `github_publications` row owns a backend-only publication claim:

```text
PUBLISHING
+ random attempt_token
+ PostgreSQL attempt_expires_at
```

Only the exact live attempt token may persist `PUBLISHED` or `FAILED`. A second request while the claim is live fails closed. After the bounded claim expires, a retry may take over with a new token; results from the old token are then stale and cannot overwrite the newer audit state.

The claim uses PostgreSQL time. Product composition caps each configured Git/GitHub operation at 30 seconds, while the publication claim lasts 300 seconds, bounding the supported retry sequence within the claim window. The claim token is never exposed through Product DTOs, SSE, generic evidence, or the browser.

## Remote branch boundary

The only publication branch namespace is:

```text
devflow/run-<run_uuid>
```

If the branch is absent, DevFlow pushes the exact evidence-selected source commit. If it already exists at the same commit, retry is idempotent. If it exists at a different commit, publication fails closed with `REMOTE_BRANCH_CONFLICT`.

The Git command never uses force push, ref deletion, or a default-branch destination. Git hooks are skipped for this backend publication operation so repository-authored hooks cannot turn a bounded publication operation into arbitrary local execution.

## Credential boundary

GitHub credentials are backend-only settings. They are never accepted in browser DTOs or requests and are never written into runtime events, prompts, generic typed evidence, Git command arguments, publication claim data, or persisted publication intent payloads.

REST authentication is sent only through the Authorization header. HTTPS Git authentication is injected into the child process environment through a temporary Git HTTP extra-header configuration, with terminal credential prompts disabled.

External errors are converted to bounded public error codes/messages; raw Git stderr and raw GitHub response bodies are not persisted or returned through the product API.

## Draft Pull Request idempotence

Before creating a PR, DevFlow searches GitHub for a Pull Request matching the deterministic DevFlow head branch and persisted base branch. An existing exact PR is reusable only when it is still **open and Draft**. A closed PR or a PR already converted out of Draft state fails closed rather than being presented as the current Draft publication.

Creation races are recovered by one bounded re-query after GitHub returns a conflicting creation response. Concurrent publication creation is additionally serialized by the PostgreSQL publication claim rather than relying on undocumented remote duplicate behavior.

Any returned PR is revalidated against:

- repository identity;
- exact DevFlow head branch;
- exact evidence-selected head commit;
- persisted base branch;
- open state;
- Draft state.

The REST boundary pins `X-GitHub-Api-Version: 2026-03-10` and validates the typed response before accepting remote facts.

## Audit persistence

`github_publications` is deliberately separate from `evidence_records` because terminal typed evidence remains append-closed after Run finalization.

The audit row contains:

- immutable `GitHubPublicationIntent` + canonical SHA-256;
- READY / PUBLISHING / FAILED / PUBLISHED publication state;
- bounded attempt count;
- backend-only attempt token/expiry while PUBLISHING;
- bounded failure code/message;
- exact PR number/URL/state/draft flag when published;
- PostgreSQL timestamps.

It is an external side-effect audit projection, not a new runtime truth source. An already-PUBLISHED row cannot be downgraded by a late failed retry, and a stale publication token cannot overwrite a newer attempt.

## Product API

```text
GET  /api/v1/runs/{run_id}/github-publication
POST /api/v1/runs/{run_id}/github-publication
```

Both reject query selectors. POST additionally rejects every non-empty request body. There are no PUT, PATCH, or DELETE publication endpoints.

## React boundary

The Dashboard does not query publication eligibility for RUNNING or FAILED Runs. A persisted SUCCEEDED Run may display backend-selected publication facts and, when backend credentials are configured, one `Create Draft PR` action.

The UI exposes no text input for SHA/ref/branch/base/title/body/token. `PUBLISHING` is a read-only backend state; the browser disables duplicate actions and polls only the publication read model until that external projection changes. A successful publish updates only the publication query; it never changes the Run query or status.

## Explicitly absent

- no GitHub-derived Run success;
- no CI/check-run-derived Reviewer bypass;
- no PR merge action;
- no branch deletion;
- no force push;
- no default branch write;
- no browser token handling;
- no arbitrary repository/ref/SHA publication;
- no benchmark/demo behavior from Step 4.8.
