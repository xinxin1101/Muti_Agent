from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from fnmatch import fnmatchcase

from app.models import ReviewDecision, ReviewerClosureContext, ReviewOutcome, TaskContract
from app.workspace import LocalGitWorkspace, WorkspaceChangeSnapshot

_MAX_CAPTURE_FILES = 64
_MAX_FILE_CHARS = 20_000
_MAX_DELTA_CHARS = 30_000


@dataclass(frozen=True)
class _ClosureFileState:
    exists: bool
    text: str | None
    truncated: bool = False


@dataclass(frozen=True)
class ReviewerClosureBaseline:
    """Runtime-owned workspace baseline captured before review-triggered repair."""

    previous_decision: ReviewDecision
    snapshot: WorkspaceChangeSnapshot
    repair_attempt_start: int
    repository_files: frozenset[str]
    file_states: dict[str, _ClosureFileState]


def capture_reviewer_closure_baseline(
    task: TaskContract,
    decision: ReviewDecision,
    *,
    workspace: LocalGitWorkspace,
    repair_attempt_start: int,
) -> ReviewerClosureBaseline:
    if decision.decision is not ReviewOutcome.CHANGES_REQUESTED:
        raise ValueError("closure baseline requires CHANGES_REQUESTED")
    if repair_attempt_start < 1:
        raise ValueError("repair_attempt_start must be positive")

    snapshot = workspace.change_snapshot()
    repository_files = frozenset(workspace.repository_files())
    prioritized: list[str] = []
    seen: set[str] = set()

    def add(path: str | None) -> None:
        if path is None:
            return
        normalized = path.strip()
        if not normalized or normalized in seen or len(prioritized) >= _MAX_CAPTURE_FILES:
            return
        try:
            workspace.resolve_path(normalized)
        except ValueError:
            return
        seen.add(normalized)
        prioritized.append(normalized)

    for issue in decision.issues:
        add(issue.file)
    for path in snapshot.changed_files:
        add(path)
    for writable in task.writable_files:
        if not any(marker in writable for marker in ("*", "?", "[")):
            add(writable)
    for path in sorted(repository_files):
        if any(fnmatchcase(path, pattern) for pattern in task.writable_files):
            add(path)

    states = {path: _capture_file_state(workspace, path) for path in prioritized}
    return ReviewerClosureBaseline(
        previous_decision=decision,
        snapshot=snapshot,
        repair_attempt_start=repair_attempt_start,
        repository_files=repository_files,
        file_states=states,
    )


def build_reviewer_closure_context(
    baseline: ReviewerClosureBaseline,
    *,
    workspace: LocalGitWorkspace,
    review_round: int,
    repair_attempt_end: int,
) -> ReviewerClosureContext:
    current = workspace.change_snapshot()
    changed_files = tuple(current.files_changed_since(baseline.snapshot))
    if not changed_files:
        raise ValueError("closure review requires workspace changes since the prior review")

    sections: list[str] = []
    for path in changed_files[:_MAX_CAPTURE_FILES]:
        before = baseline.file_states.get(path)
        if before is None:
            existed = path in baseline.repository_files
            before = _ClosureFileState(
                exists=existed,
                text=None if existed else "",
            )
        after = _capture_file_state(workspace, path)
        sections.append(_file_delta(path, before, after))

    if len(changed_files) > _MAX_CAPTURE_FILES:
        sections.append(
            f"[repair delta omitted {len(changed_files) - _MAX_CAPTURE_FILES} "
            "additional changed files]"
        )
    repair_delta = "\n\n".join(section for section in sections if section).strip()
    if not repair_delta:
        repair_delta = "[repair delta unavailable; runtime patch hash confirms workspace mutation]"

    return ReviewerClosureContext(
        review_round=review_round,
        previous_decision=baseline.previous_decision,
        repair_attempt_start=baseline.repair_attempt_start,
        repair_attempt_end=repair_attempt_end,
        repair_changed_files=changed_files[:_MAX_CAPTURE_FILES],
        patch_hash_before=baseline.snapshot.patch_hash,
        patch_hash_after=current.patch_hash,
        repair_delta=repair_delta[:_MAX_DELTA_CHARS],
    )


def _capture_file_state(workspace: LocalGitWorkspace, path: str) -> _ClosureFileState:
    try:
        resolved = workspace.resolve_path(path)
    except ValueError:
        return _ClosureFileState(exists=False, text=None)
    if not resolved.is_file():
        return _ClosureFileState(exists=False, text="")
    try:
        text = resolved.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return _ClosureFileState(exists=True, text=None)
    truncated = len(text) > _MAX_FILE_CHARS
    return _ClosureFileState(
        exists=True,
        text=text[:_MAX_FILE_CHARS],
        truncated=truncated,
    )


def _file_delta(path: str, before: _ClosureFileState, after: _ClosureFileState) -> str:
    if before.exists and before.text is None:
        return (
            f"Repair delta for {path}: pre-repair text unavailable; inspect current full diff."
        )
    if after.exists and after.text is None:
        return (
            f"Repair delta for {path}: post-repair text unavailable; inspect current full diff."
        )

    before_text = before.text or ""
    after_text = after.text or ""
    patch = "\n".join(
        unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=f"a/{path}" if before.exists else "/dev/null",
            tofile=f"b/{path}" if after.exists else "/dev/null",
            lineterm="",
        )
    )
    if before.truncated or after.truncated:
        patch += "\n[bounded repair delta: one or both file snapshots were truncated]"
    return patch or f"Repair delta for {path}: file metadata changed without textual delta."
