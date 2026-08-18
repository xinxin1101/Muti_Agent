# Step 3.3 — AST / Import-aware Relevant-code Extraction

## Purpose

Step 3.3 improves **which repository regions are selected** before `ContextPacketBuilder`
spends the already-accepted Step 3.2 content budget. It does not replace the
`ContextPacket` protocol or any runtime trust boundary.

```text
TaskContract
      +
Current managed worktree
      ↓
RelevantCodeExtractor
      ├── Python AST symbols
      ├── local import edges
      ├── deterministic lexical task terms
      ├── task-scope candidate filtering
      └── bounded internal ranking evidence
      ↓
RelevantFileSelection / RelevantCodeRegion
      ↓
ContextPacketBuilder
      ├── trusted path reads
      ├── source byte / UTF-8 checks
      ├── existing file/char/token budgets
      ├── existing truncation evidence
      ├── existing Git/path/scope provenance
      └── existing canonical fingerprint
      ↓
ContextPacket
```

## Frozen protocol boundary

Step 3.3 does **not** add fields to `ContextPacket`, `ContextFile`, or
`ContextSnippet`.

The selector emits an internal artifact:

- `RelevantFileSelection`
  - repository path;
  - deterministic integer score;
  - selected `RelevantCodeRegion` values;
  - one-hop local dependency paths;
  - deterministic internal evidence strings;
  - whether Python AST indexing succeeded.
- `RelevantCodeRegion`
  - start/end line;
  - region kind;
  - symbol/qualified name when applicable;
  - deterministic score;
  - deterministic selection evidence.

The Builder compiles those regions into the already-accepted
`ContextSnippet(start_line, end_line, content, char_count, estimated_tokens)`
contract. The packet therefore remains fingerprint-compatible with the Step 3.2
schema while its `selection_strategy` / `snippet_strategy` metadata identifies the
new policy.

## Language boundary

The first accepted implementation is intentionally **Python-aware**, using the
Python standard-library `ast` parser.

- `.py` files may receive AST symbol/import selection.
- invalid Python syntax fails open only at the relevance layer: the file falls
  back to the existing deterministic prefix strategy.
- non-Python text also uses the existing deterministic prefix strategy.
- source-size, UTF-8, path, symlink, and scope decisions are not delegated to the
  AST layer.

This avoids falsely claiming language support that has not been implemented.
Additional language analyzers can be introduced later behind the same internal
selector boundary.

## Deterministic relevance policy

The extractor derives provider-neutral lexical terms from:

- task objective;
- acceptance criteria;
- readable/writable/read-only patterns.

Common low-information English terms are removed. Identifier terms are split on
snake_case and camelCase boundaries.

Baseline file score preserves accepted runtime facts:

1. changed-file bonus;
2. writable-scope bonus;
3. read-only/readable scope bonuses;
4. task-term overlap with repository path.

Python AST then adds symbol-level relevance:

- task-term overlap with function/class/method qualified names;
- imported-symbol overlap for local dependency files;
- a small top-level-symbol bonus only after actual task/import relevance exists;
- a separate top-level fallback for changed/writable Python files only when no
  lexical symbol matches.

The top-level bonus cannot create relevance by itself. Unrelated definitions are
therefore not selected merely because they are top-level declarations.

Overlapping class/method regions are de-duplicated before packet construction. The
higher-ranked overlapping symbol wins, avoiding redundant nested definitions and
keeping selected-region provenance precise.

The module docstring/import preamble is retained as a small region when present,
so selected definitions keep their local import context.

## Local import edges

The selector constructs module aliases only from **TaskContract-visible Python
candidate paths**. Imports therefore cannot widen readable scope.

Supported first-version cases include:

- `import package.module`;
- `from package.module import Symbol`;
- relative imports such as `from .models import User`.

Only one-hop local dependency expansion is performed in Step 3.3. External
packages are ignored because they have no visible local candidate path.

Import resolution prefers the most specific visible module alias. If an alias maps
to more than one visible repository path, resolution fails closed for that edge:
DevFlow does not choose a dependency by path ordering or guess which module the
runtime would import.

A local dependency receives a bounded score derived from the strongest importing
visible parent. Explicit `from ... import Symbol` names are also used to select
the corresponding definition region when possible.

The AST index itself is bounded:

- maximum initially indexed Python files;
- maximum additional one-hop dependency files;
- maximum selected symbol regions per file.

These are selector-internal execution bounds and do not replace `ContextBudget`.

## Builder trust boundary remains authoritative

`RelevantCodeExtractor` receives source text only through a Builder-owned loader.
The Builder still performs:

- repository-relative path resolution;
- managed-worktree containment;
- regular-file checks;
- maximum source-file byte checks;
- UTF-8 validation.

If a file is too large, non-UTF-8, unavailable, or outside task-visible scope, AST
selection cannot bypass that fact.

## Region budgeting

For AST-selected Python files, `ContextPacketBuilder` spends the existing budget
on selected regions rather than on an unconditional prefix.

The existing truncation taxonomy remains unchanged:

- `PER_FILE_CHAR_LIMIT`;
- `TOTAL_CHAR_LIMIT`;
- `TOKEN_BUDGET`;
- `FILE_COUNT_LIMIT`;
- `SOURCE_FILE_TOO_LARGE`;
- `NON_UTF8`;
- `PATH_UNAVAILABLE`.

Intentional omission of unrelated source outside selected AST regions is **not**
reported as budget truncation. `ContextFile.truncated` remains a budget-clipping
fact, not a statement that the whole source file was included.

The provider-neutral `utf8_bytes_upper_bound` estimator remains unchanged.

## Determinism and auditability

For the same:

- `TaskContract`;
- visible candidate inventory;
- source contents;
- selector bounds;
- `ContextBudget`;

selection ordering, regions, `ContextPacket`, and packet fingerprint are stable.
No model call, embedding service, vector database, random number, wall clock, or
network request participates in relevance selection.

Determinism is not permission to guess: ambiguous local-import aliases deliberately
produce no dependency edge instead of selecting the lexicographically first path.

## Explicitly out of scope

Step 3.3 does not introduce:

- embeddings or vector retrieval;
- LLM-based code selection;
- call-graph or dynamic execution analysis;
- PostgreSQL;
- Redis/Dramatiq;
- lease/heartbeat/run-token behavior;
- frontend behavior;
- changes to Docker verification, Reviewer, Repair, DAG, worktree, merge queue,
  conflict classification, or Human Gate semantics.
