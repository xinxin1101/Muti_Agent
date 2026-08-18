# Step 3.3 Acceptance Plan

Step 3.3 is accepted only when the production `ContextPacketBuilder` uses the
AST/import-aware selector without weakening any Step 3.2 trust or budget boundary.

Required checks:

1. Python task-symbol matching selects the relevant definition rather than an
   unrelated same-file definition.
2. A visible one-hop local import can raise the imported dependency ahead of an
   unrelated file when the packet file budget is tight.
3. `from ... import Symbol` selects the corresponding visible definition when
   present.
4. Relative local imports resolve deterministically.
5. Imports to paths outside TaskContract-visible scope never widen context.
6. Invalid Python syntax falls back to deterministic prefix selection.
7. Non-Python text continues to use deterministic prefix selection.
8. AST pre-scan cannot bypass source-size, UTF-8, path, or managed-worktree checks.
9. AST-selected regions still obey existing per-file, total-character, token-unit,
   file-count, and source-size budgets with existing truncation types.
10. Same task + same worktree + same bounds produces deterministic region ordering
    and the same ContextPacket fingerprint.
11. Developer / Repair / Reviewer stage-local packet rebuild behavior remains green.
12. Existing Docker verification, Reviewer, Repair, DAG, worktree, merge, conflict,
    and Human Gate regression tests remain green.
13. `backend/app/models/context.py` remains unchanged in Step 3.3.
14. Final GitHub Actions Backend Quality must pass with zero skipped tests.
