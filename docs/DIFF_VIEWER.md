# Step 4.5 — Read-only Diff Viewer

Step 4.5 exposes already accepted Git facts to the product UI without creating a second Git authority.

## Authority model

```text
accepted typed Git evidence
        ↓
backend commit-pair resolver
        ↓
Git object + parent-chain validation
        ↓
ReadOnlyCommitDiffReader
        ↓
bounded file/stat/patch DTO
        ↓
GET task diff
        ↓
React DiffViewer
```

The browser may choose only which accepted evidence projection it wants to inspect:

- `TASK` — the successful `WORKER_EXECUTION` task commit relative to its recorded task base;
- `INTEGRATION` — the successful `MERGE_QUEUE_SNAPSHOT` integration commit relative to its recorded previous integration head.

The browser does **not** supply `base_commit`, `head_commit`, branch names, refs, worktree paths, or Git mutation instructions. The HTTP boundary rejects unexpected diff query parameters rather than silently accepting SHA injection attempts.

## Task diff evidence

A task diff is available only after typed `WORKER_EXECUTION` evidence records a successful execution with a task commit. The backend re-validates:

```text
persisted Run / Task identity
        ↓
successful WorkerExecutionEvidence
        ↓
recorded base_commit + commit_sha
        ↓
Git commit exists exactly
        ↓
parents(commit_sha) == (base_commit,)
```

Missing successful evidence is an unavailable projection, not an invitation for the browser to choose another commit. Conflicting successful commit pairs fail closed as persistence corruption.

## Integration diff evidence

An integration diff is derived only from a typed `MERGE_QUEUE_SNAPSHOT`. The backend re-validates:

```text
MergeQueueSnapshot.run_base_commit == Run.base_commit
        ↓
INTEGRATED attempt for Task
        ↓
task parents == (task_base_commit,)
        ↓
integration parents ==
(previous_integration_commit, task_commit)
```

The displayed comparison is `previous_integration_commit → integration_commit`. A missing integration attempt returns an unavailable projection. Conflicting or irreproducible evidence fails closed.

## Bounded Git extraction

`ReadOnlyCommitDiffReader` executes only read-only Git object/diff commands and never calls `update-ref`, `checkout`, `switch`, `reset`, `add`, `commit`, `commit-tree`, `merge`, or worktree mutation operations.

Current output bounds are:

- at most 100 rendered changed-file records;
- at most 64 KiB of patch bytes per file;
- at most 256 KiB of rendered patch bytes in one response;
- text patch extraction is omitted for blobs larger than 512 KiB;
- each Git command has a 10 second timeout;
- rename detection is disabled so changed paths remain explicit;
- binary files expose metadata but not binary patch bodies.

Patch text is presentation evidence only. When a patch is bounded, the DTO explicitly reports truncation or omission; the UI never presents bounded content as the complete artifact.

## Product API

```text
GET /api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=TASK
GET /api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=INTEGRATION
```

Only one optional `kind` selector is accepted. Unknown selectors fail schema validation. Extra query parameters fail closed.

The typed response includes evidence identity/hash, exact validated commit pair, file status/statistics, bounded patch text, patch hash, omission reason, and truncation metadata.

## React boundary

The Task Detail page may:

- switch between Task and Integration evidence views;
- render changed-file metadata and unified patch text;
- show exact commit identities and bounded-output warnings.

It may not:

- edit patch text or repository files;
- stage/unstage changes;
- create/amend commits;
- reset/checkout/switch refs;
- merge/cherry-pick/rebase;
- change task commits or integration refs;
- authorize scheduler/integration progress.

React renders patch text as text nodes; it does not interpret patch content as HTML or executable instructions.

## Frozen Step 4.5 principle

> **Persisted typed evidence chooses the commit pair; Git proves the code delta; the browser only renders the bounded result.**

Step 4.5 deliberately does not add Run Metrics, GitHub branch/Draft PR publication, or benchmark/demo behavior. Those remain Steps 4.6–4.8.