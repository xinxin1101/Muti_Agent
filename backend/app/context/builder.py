from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.models.context import (
    ContextBudget,
    ContextFile,
    ContextPacket,
    ContextScopeKind,
    ContextScopeMatch,
    ContextSelectionReason,
    ContextSnippet,
    ContextTruncation,
    ContextTruncationReason,
    ContextUsage,
)
from app.models.task import TaskContract
from app.workspace import LocalGitWorkspace, ScopeEnforcer, WorkspaceGitError


class ContextBuildError(RuntimeError):
    """Raised when trusted repository state cannot be compiled into a context packet."""


@dataclass(frozen=True)
class _Candidate:
    path: str
    tracked: bool
    changed: bool
    scope_matches: tuple[ContextScopeMatch, ...]
    selection_reasons: tuple[ContextSelectionReason, ...]
    priority: int


class ContextPacketBuilder:
    """Compile deterministic, bounded repository context from current worktree truth."""

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        scope_enforcer: ScopeEnforcer | None = None,
    ) -> None:
        self._budget = budget or ContextBudget()
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    def build(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> ContextPacket:
        try:
            repository_head = workspace.head_commit()
            tracked = set(workspace.tracked_files())
            inventory = workspace.repository_files()
            changed_files = workspace.changed_files()
        except WorkspaceGitError as exc:
            raise ContextBuildError(f"Unable to read trusted Git state: {exc}") from exc

        changed = set(changed_files)
        candidates = self._candidates(
            task,
            inventory=inventory,
            tracked=tracked,
            changed=changed,
        )

        selected_files: list[ContextFile] = []
        truncations: list[ContextTruncation] = []
        omitted_files = 0
        remaining_chars = self._budget.max_total_chars
        remaining_tokens = self._budget.max_estimated_tokens

        for index, candidate in enumerate(candidates):
            remaining_candidates = len(candidates) - index
            if len(selected_files) >= self._budget.max_files:
                omitted_files += remaining_candidates
                truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.FILE_COUNT_LIMIT,
                        detail=(
                            "Remaining visible files were omitted because the context file-count "
                            f"budget of {self._budget.max_files} was exhausted."
                        ),
                        omitted_files=remaining_candidates,
                    )
                )
                break
            if remaining_chars <= 0:
                omitted_files += remaining_candidates
                truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOTAL_CHAR_LIMIT,
                        detail=(
                            "Remaining visible files were omitted because the total context "
                            "character budget was exhausted."
                        ),
                        omitted_files=remaining_candidates,
                    )
                )
                break
            if remaining_tokens <= 0:
                omitted_files += remaining_candidates
                truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOKEN_BUDGET,
                        detail=(
                            "Remaining visible files were omitted because the conservative token "
                            "budget was exhausted."
                        ),
                        omitted_files=remaining_candidates,
                    )
                )
                break

            loaded = self._load_candidate(
                candidate,
                workspace=workspace,
                remaining_chars=remaining_chars,
                remaining_tokens=remaining_tokens,
            )
            if isinstance(loaded, ContextTruncation):
                omitted_files += 1
                truncations.append(loaded)
                continue

            context_file, file_truncations = loaded
            selected_files.append(context_file)
            truncations.extend(file_truncations)
            remaining_chars -= context_file.selected_chars
            remaining_tokens -= context_file.estimated_tokens

        usage = ContextUsage(
            candidate_files=len(candidates),
            selected_files=len(selected_files),
            selected_chars=sum(item.selected_chars for item in selected_files),
            estimated_tokens=sum(item.estimated_tokens for item in selected_files),
            truncated_files=sum(1 for item in selected_files if item.truncated),
            omitted_files=omitted_files,
        )

        payload = {
            "task_id": task.task_id,
            "objective": task.objective,
            "acceptance_criteria": task.acceptance_criteria,
            "readable_files": task.readable_files,
            "writable_files": task.writable_files,
            "readonly_files": task.readonly_files,
            "repository_head": repository_head,
            "changed_files": changed_files,
            "selected_files": [item.model_dump(mode="json") for item in selected_files],
            "truncations": [item.model_dump(mode="json") for item in truncations],
            "budget": self._budget.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "selection_strategy": "changed>writable>read_only>readable>path",
            "snippet_strategy": "deterministic_prefix",
            "token_estimator": "utf8_bytes_upper_bound",
        }
        fingerprint = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        return ContextPacket(**payload, fingerprint=fingerprint)

    def _candidates(
        self,
        task: TaskContract,
        *,
        inventory: list[str],
        tracked: set[str],
        changed: set[str],
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for path in inventory:
            scope_matches = self._scope_matches(task, path)
            if not scope_matches:
                continue
            selection_reasons = self._selection_reasons(path, scope_matches, changed)
            candidates.append(
                _Candidate(
                    path=path,
                    tracked=path in tracked,
                    changed=path in changed,
                    scope_matches=tuple(scope_matches),
                    selection_reasons=tuple(selection_reasons),
                    priority=self._priority(path, scope_matches, changed),
                )
            )
        return sorted(candidates, key=lambda item: (item.priority, item.path))

    def _scope_matches(self, task: TaskContract, path: str) -> list[ContextScopeMatch]:
        matches: list[ContextScopeMatch] = []
        ordered_scopes = (
            (ContextScopeKind.WRITABLE, task.writable_files),
            (ContextScopeKind.READ_ONLY, task.readonly_files),
            (ContextScopeKind.READABLE, task.readable_files),
        )
        for kind, patterns in ordered_scopes:
            for pattern in patterns:
                if self._scope_enforcer.matches(path, pattern):
                    matches.append(ContextScopeMatch(kind=kind, pattern=pattern))
        return matches

    @staticmethod
    def _selection_reasons(
        path: str,
        scope_matches: list[ContextScopeMatch],
        changed: set[str],
    ) -> list[ContextSelectionReason]:
        reasons: list[ContextSelectionReason] = []
        if path in changed:
            reasons.append(ContextSelectionReason.CHANGED)
        kinds = {match.kind for match in scope_matches}
        if ContextScopeKind.WRITABLE in kinds:
            reasons.append(ContextSelectionReason.WRITABLE_SCOPE)
        if ContextScopeKind.READ_ONLY in kinds:
            reasons.append(ContextSelectionReason.READ_ONLY_SCOPE)
        if ContextScopeKind.READABLE in kinds:
            reasons.append(ContextSelectionReason.READABLE_SCOPE)
        return reasons

    @staticmethod
    def _priority(
        path: str,
        scope_matches: list[ContextScopeMatch],
        changed: set[str],
    ) -> int:
        if path in changed:
            return 0
        kinds = {match.kind for match in scope_matches}
        if ContextScopeKind.WRITABLE in kinds:
            return 1
        if ContextScopeKind.READ_ONLY in kinds:
            return 2
        return 3

    def _load_candidate(
        self,
        candidate: _Candidate,
        *,
        workspace: LocalGitWorkspace,
        remaining_chars: int,
        remaining_tokens: int,
    ) -> tuple[ContextFile, list[ContextTruncation]] | ContextTruncation:
        try:
            path = workspace.resolve_path(candidate.path)
        except ValueError as exc:
            return ContextTruncation(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                path=candidate.path,
                detail=f"Visible path was rejected by the trusted workspace boundary: {exc}",
                omitted_files=1,
            )

        if not path.is_file():
            return ContextTruncation(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                path=candidate.path,
                detail="Visible Git path is not a regular readable file in the current worktree.",
                omitted_files=1,
            )

        try:
            size = path.stat().st_size
        except OSError as exc:
            return ContextTruncation(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                path=candidate.path,
                detail=f"Visible file metadata could not be read: {exc}",
                omitted_files=1,
            )
        if size > self._budget.max_source_file_bytes:
            return ContextTruncation(
                reason=ContextTruncationReason.SOURCE_FILE_TOO_LARGE,
                path=candidate.path,
                detail=(
                    f"Source file size {size} bytes exceeds the inspection limit of "
                    f"{self._budget.max_source_file_bytes} bytes."
                ),
                omitted_files=1,
            )

        try:
            raw = path.read_bytes()
        except OSError as exc:
            return ContextTruncation(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                path=candidate.path,
                detail=f"Visible file bytes could not be read: {exc}",
                omitted_files=1,
            )
        if len(raw) > self._budget.max_source_file_bytes:
            return ContextTruncation(
                reason=ContextTruncationReason.SOURCE_FILE_TOO_LARGE,
                path=candidate.path,
                detail=(
                    f"Source file size {len(raw)} bytes exceeds the inspection limit of "
                    f"{self._budget.max_source_file_bytes} bytes."
                ),
                omitted_files=1,
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ContextTruncation(
                reason=ContextTruncationReason.NON_UTF8,
                path=candidate.path,
                detail="Visible file is not valid UTF-8 text and was omitted from model context.",
                omitted_files=1,
            )

        char_limit = min(self._budget.max_chars_per_file, remaining_chars)
        selected = self._fit_prefix(
            text,
            max_chars=char_limit,
            max_token_units=remaining_tokens,
        )
        if text and not selected:
            return ContextTruncation(
                reason=ContextTruncationReason.TOKEN_BUDGET,
                path=candidate.path,
                detail=(
                    "No complete UTF-8 character from this file fit inside the remaining "
                    "conservative token budget."
                ),
                omitted_chars=len(text),
                omitted_files=1,
            )

        selected_tokens = len(selected.encode("utf-8"))
        line_count = max(1, len(selected.splitlines()))
        snippet = ContextSnippet(
            start_line=1,
            end_line=line_count,
            content=selected,
            char_count=len(selected),
            estimated_tokens=selected_tokens,
        )
        truncated = len(selected) < len(text)
        context_file = ContextFile(
            path=candidate.path,
            tracked=candidate.tracked,
            changed=candidate.changed,
            scope_matches=list(candidate.scope_matches),
            selection_reasons=list(candidate.selection_reasons),
            source_sha256=sha256(raw).hexdigest(),
            source_chars=len(text),
            source_bytes=len(raw),
            snippets=[snippet],
            selected_chars=len(selected),
            estimated_tokens=selected_tokens,
            truncated=truncated,
        )

        file_truncations: list[ContextTruncation] = []
        if truncated:
            omitted_chars = len(text) - len(selected)
            if self._budget.max_chars_per_file < len(text):
                file_truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.PER_FILE_CHAR_LIMIT,
                        path=candidate.path,
                        detail=(
                            "File content was truncated by the per-file character budget of "
                            f"{self._budget.max_chars_per_file}."
                        ),
                        omitted_chars=omitted_chars,
                    )
                )
            if remaining_chars < len(text):
                file_truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOTAL_CHAR_LIMIT,
                        path=candidate.path,
                        detail="File content was truncated by the remaining total character budget.",
                        omitted_chars=omitted_chars,
                    )
                )
            if remaining_tokens < len(raw):
                file_truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOKEN_BUDGET,
                        path=candidate.path,
                        detail=(
                            "File content was truncated by the remaining conservative token "
                            "budget."
                        ),
                        omitted_chars=omitted_chars,
                    )
                )

        return context_file, file_truncations

    @staticmethod
    def _fit_prefix(text: str, *, max_chars: int, max_token_units: int) -> str:
        if not text or max_chars <= 0 or max_token_units <= 0:
            return ""
        candidate = text[:max_chars]
        if len(candidate.encode("utf-8")) <= max_token_units:
            return candidate

        low = 0
        high = len(candidate)
        while low < high:
            midpoint = (low + high + 1) // 2
            if len(candidate[:midpoint].encode("utf-8")) <= max_token_units:
                low = midpoint
            else:
                high = midpoint - 1
        return candidate[:low]
