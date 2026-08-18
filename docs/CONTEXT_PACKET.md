# Step 3.2 — Context Packet Builder

## Purpose

Step 3.2 introduces a bounded, deterministic, auditable repository-context packet for coding agents.

The runtime already owns task contracts, Git/worktree state, scope enforcement, deterministic verification, semantic review, repair, and integration evidence. Step 3.2 adds one new boundary:

```text
TaskContract
+
Trusted managed worktree state
        |
        v
ContextPacketBuilder
        |
        +-- task objective / acceptance criteria / scopes
        +-- selected repository files
        +-- line-addressable snippets
        +-- scope/path provenance
        +-- deterministic ordering
        +-- content budgets
        +-- truncation / omission evidence
        +-- packet fingerprint
        |
        v
Bounded ContextPacket
        |
        +--> Developer
        +--> Reviewer
        +--> Repair
```

Repository content inside a packet is **untrusted data**. Runtime-generated provenance, Git identity, scope matches, budgets, and fingerprints are control-plane evidence.

## Explicit Step 3.2 boundary

Step 3.2 does **not** implement semantic code retrieval, embeddings, AST traversal, import/dependency expansion, call-graph analysis, or symbol ranking. Those belong to the later AST/import-aware extraction step.

The initial selection policy is deliberately mechanical and reproducible:

1. current Git-changed visible files;
2. writable-scope visible files;
3. read-only-scope visible files;
4. readable-scope visible files;
5. repository-relative path as the final stable tie-breaker.

A file may match several scope classes; all matches are preserved as provenance.

## Trusted repository source

The builder may only inventory files from the managed `LocalGitWorkspace` using Git-tracked plus non-ignored untracked paths. `.git` internals and symbolic-link traversal remain outside the trusted path boundary.

Each packet records:

- exact `HEAD` commit;
- current changed-file list;
- selected repository-relative paths;
- whether a selected path is tracked and/or changed;
- matching task-scope patterns;
- SHA-256 of the current full source bytes for selected text files.

The SHA-256 binds a snippet to the exact worktree bytes from which it was derived. It does not make repository content trusted instructions.

## Budget model

`ContextBudget` independently bounds:

- number of selected files;
- characters selected from one file;
- total selected characters;
- conservative token estimate;
- maximum source-file bytes that the builder will inspect as text.

The first implementation uses the deterministic estimator `utf8_bytes_upper_bound`: selected UTF-8 byte count is recorded as a conservative token-budget unit. This is intentionally provider-neutral and auditable; it is not presented as an exact tokenizer count.

If a model-specific tokenizer is introduced later, that must be an explicit policy change rather than a silent replacement of the estimator.

## Snippet strategy

Step 3.2 uses deterministic prefix snippets. A selected snippet records exact start/end line provenance and its character/token-unit usage.

This is intentionally less clever than AST-aware selection. Developer and Repair retain the already accepted controlled repository tools for additional on-demand reads. Reviewer additionally retains the accepted Git diff plus deterministic verification evidence.

## Truncation and omission evidence

The builder never silently drops context. It records bounded evidence when content is omitted or truncated because of:

- per-file character budget;
- total character budget;
- token-estimate budget;
- file-count budget;
- source-file size limit;
- non-UTF-8 content;
- unsafe/unavailable repository path.

## Fingerprint

A `ContextPacket` receives a SHA-256 fingerprint over its canonical runtime-generated payload, including task context, Git identity, selected snippets, provenance, budgets, usage, and truncation evidence.

For the same task, worktree state, and budget policy, packet construction must be deterministic and produce the same fingerprint. A relevant worktree-content change must change the fingerprint.

## Agent integration

The production `SingleTaskOrchestrator` owns packet construction at the stage boundary:

- before initial Developer execution;
- before each Repair attempt;
- after deterministic verification passes and before Reviewer execution.

This deliberately rebuilds context from current repository truth after mutations rather than carrying Developer conversation history forward.

Agent APIs may retain an optional packet argument for isolated unit tests/backward-compatible direct invocation, but the production orchestrator path must supply a runtime-built packet.

The packet does not certify correctness and cannot set task success. Existing Git scope, deterministic verification, independent review, and bounded repair gates remain authoritative.
