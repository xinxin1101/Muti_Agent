from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path

from app.context.relevance import (
    RelevanceCandidate,
    RelevantCodeExtractor,
    RelevantCodeRegion,
)
from app.context.token_estimator import TokenEstimator
from app.models.context import (
    ContextBudget,
    ContextContinuationState,
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
    relevance_score: int = 0
    regions: tuple[RelevantCodeRegion, ...] = ()


@dataclass(frozen=True)
class _LoadedSource:
    raw: bytes
    text: str


@dataclass(frozen=True)
class _SourceFailure:
    reason: ContextTruncationReason
    detail: str


_SourceResult = _LoadedSource | _SourceFailure


class ContextPacketBuilder:
    """Compile deterministic, bounded repository context from current worktree truth."""

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        scope_enforcer: ScopeEnforcer | None = None,
        relevance_extractor: RelevantCodeExtractor | None = None,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._budget = budget or ContextBudget()
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._relevance_extractor = relevance_extractor or RelevantCodeExtractor()
        self._token_estimator = token_estimator or TokenEstimator()

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    def build(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        resume: ContextContinuationState | None = None,
    ) -> ContextPacket:
        try:
            repository_head = workspace.head_commit()
            tracked = set(workspace.tracked_files())
            inventory = workspace.repository_files()
            changed_files = workspace.changed_files()
        except WorkspaceGitError as exc:
            raise ContextBuildError(f"Unable to read trusted Git state: {exc}") from exc

        # A resumed worktree is clean because the previous slice has already committed its
        # bounded patch. Keep those changed paths visible without replaying every earlier read.
        resumed_changes = set(resume.changed_files) if resume is not None else set()
        changed = set(changed_files) | resumed_changes
        base_candidates = self._base_candidates(
            task,
            inventory=inventory,
            tracked=tracked,
            changed=changed,
        )
        source_cache: dict[str, _SourceResult] = {}
        candidates = self._rank_candidates(
            task,
            base_candidates,
            workspace=workspace,
            source_cache=source_cache,
        )

        selected_files: list[ContextFile] = []
        truncations: list[ContextTruncation] = []
        omitted_files = 0
        reused_files = 0
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

            if self._can_reuse_candidate(
                candidate,
                resume=resume,
                changed=changed,
                workspace=workspace,
                source_cache=source_cache,
            ):
                reused_files += 1
                continue
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
                source_cache=source_cache,
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

        repository_summary = self._repository_summary(repository_head, inventory)
        usage = ContextUsage(
            candidate_files=len(candidates),
            selected_files=len(selected_files),
            selected_chars=sum(item.selected_chars for item in selected_files),
            estimated_tokens=sum(item.estimated_tokens for item in selected_files),
            context_window_units=sum(item.estimated_tokens for item in selected_files),
            billable_content_tokens=sum(
                item.billable_token_estimate for item in selected_files
            ),
            billable_prompt_tokens=(
                sum(item.billable_token_estimate for item in selected_files)
                + self._token_estimator.billable_token_estimate(repository_summary)
                + self._resume_billable_tokens(resume)
            ),
            truncated_files=sum(1 for item in selected_files if item.truncated),
            omitted_files=omitted_files,
            reused_files=reused_files,
            trimmed_files=sum(1 for item in selected_files if item.truncated),
            prompt_estimated_tokens=(
                # Deprecated compatibility field. It remains the safety-bound estimate for
                # old read-only consumers and must not be passed to token reservations.
                sum(item.estimated_tokens for item in selected_files)
                + self._token_estimator.context_window_units(repository_summary)
                + self._resume_estimated_tokens(resume)
            ),
        )

        payload = {
            "task_id": task.task_id,
            "objective": task.objective,
            "acceptance_criteria": task.acceptance_criteria,
            "readable_files": task.readable_files,
            "writable_files": task.writable_files,
            "readonly_files": task.readonly_files,
            "repository_head": repository_head,
            "repository_summary_version": "repository_summary_v1",
            "repository_summary": repository_summary,
            "resume": resume.model_dump(mode="json") if resume is not None else None,
            "changed_files": sorted(changed),
            # A compact, path-only map gives the Agent enough orientation to choose its first
            # batch read without exposing more repository content or consuming another model turn.
            "repository_map": sorted(candidate.path for candidate in base_candidates)[:80],
            "selected_files": [item.model_dump(mode="json") for item in selected_files],
            "truncations": [item.model_dump(mode="json") for item in truncations],
            "budget": self._budget.model_dump(mode="json"),
            "usage": usage.model_dump(mode="json"),
            "selection_strategy": (
                "python_ast_import_relevance_v1>changed>writable>read_only>readable>path"
            ),
            "snippet_strategy": ("python_ast_symbol_regions_v1+deterministic_prefix_fallback"),
            "token_estimator": "utf8_bytes_upper_bound+provider_neutral_billable_v1",
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

    def _can_reuse_candidate(
        self,
        candidate: _Candidate,
        *,
        resume: ContextContinuationState | None,
        changed: set[str],
        workspace: LocalGitWorkspace,
        source_cache: dict[str, _SourceResult],
    ) -> bool:
        if resume is None:
            return False
        # A fenced checkpoint already records the content identity of files changed in
        # the prior slice.  Do not replay their source merely because they are marked
        # changed: the next Agent can explicitly read one if it needs implementation
        # detail.  This keeps recovery context to facts, hashes and unresolved work.
        if candidate.path in resume.changed_files:
            return True
        if candidate.path in changed:
            return False
        previous = {item.path: item.source_sha256 for item in resume.read_files}
        expected_hash = previous.get(candidate.path)
        if expected_hash is None:
            return False
        source = self._read_source(
            candidate.path,
            workspace=workspace,
            source_cache=source_cache,
        )
        return isinstance(source, _LoadedSource) and sha256(source.raw).hexdigest() == expected_hash

    def _resume_estimated_tokens(self, resume: ContextContinuationState | None) -> int:
        if resume is None:
            return 0
        text = "\n".join(
            (
                resume.completed_summary,
                resume.remaining_summary,
                resume.verification_summary,
                resume.failure_summary,
            )
        )
        return self._token_estimator.context_window_units(text)

    def _resume_billable_tokens(self, resume: ContextContinuationState | None) -> int:
        if resume is None:
            return 0
        return self._token_estimator.billable_token_estimate(
            "\n".join(
                (
                    resume.completed_summary,
                    resume.remaining_summary,
                    resume.verification_summary,
                    resume.failure_summary,
                )
            )
        )

    @staticmethod
    def _repository_summary(repository_head: str, inventory: list[str]) -> str:
        dependency_files = [
            path
            for path in inventory
            if Path(path).name
            in {
                "pyproject.toml",
                "requirements.txt",
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "go.mod",
                "Cargo.toml",
            }
        ]
        entry_files = [
            path
            for path in inventory
            if Path(path).name
            in {"main.py", "app.py", "server.py", "index.js", "index.ts", "main.ts", "main.tsx"}
        ]
        test_files = [
            path
            for path in inventory
            if "/test" in f"/{path.lower()}" or Path(path).name.startswith("test_")
        ]
        technologies = []
        names = {Path(path).name for path in inventory}
        if {"pyproject.toml", "requirements.txt"} & names:
            technologies.append("Python")
        if {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"} & names:
            technologies.append("Node.js")
        if "go.mod" in names:
            technologies.append("Go")
        if "Cargo.toml" in names:
            technologies.append("Rust")
        return "\n".join(
            (
                "summary_version=repository_summary_v1",
                f"repository_head={repository_head}",
                f"technology={','.join(technologies) or 'unknown'}",
                f"entry_files={','.join(entry_files[:12]) or 'unknown'}",
                f"dependency_files={','.join(dependency_files[:12]) or 'none'}",
                f"test_file_count={len(test_files)}",
                f"directory_index={','.join(sorted(inventory)[:80])}",
            )
        )

    def _rank_candidates(
        self,
        task: TaskContract,
        candidates: list[_Candidate],
        *,
        workspace: LocalGitWorkspace,
        source_cache: dict[str, _SourceResult],
    ) -> list[_Candidate]:
        candidate_by_path = {candidate.path: candidate for candidate in candidates}
        relevance_candidates = [
            RelevanceCandidate(
                path=candidate.path,
                changed=candidate.changed,
                scope_kinds=tuple(dict.fromkeys(match.kind for match in candidate.scope_matches)),
            )
            for candidate in candidates
        ]

        selections = self._relevance_extractor.select(
            task,
            relevance_candidates,
            load_source=lambda path: self._source_text(
                path,
                workspace=workspace,
                source_cache=source_cache,
            ),
        )
        return [
            replace(
                candidate_by_path[selection.path],
                relevance_score=selection.score,
                regions=selection.regions,
            )
            for selection in selections
        ]

    def _source_text(
        self,
        path: str,
        *,
        workspace: LocalGitWorkspace,
        source_cache: dict[str, _SourceResult],
    ) -> str | None:
        result = self._read_source(
            path,
            workspace=workspace,
            source_cache=source_cache,
        )
        if isinstance(result, _SourceFailure):
            return None
        return result.text

    def _base_candidates(
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

    def _read_source(
        self,
        path: str,
        *,
        workspace: LocalGitWorkspace,
        source_cache: dict[str, _SourceResult],
    ) -> _SourceResult:
        cached = source_cache.get(path)
        if cached is not None:
            return cached

        try:
            resolved = workspace.resolve_path(path)
        except ValueError as exc:
            result: _SourceResult = _SourceFailure(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                detail=f"Visible path was rejected by the trusted workspace boundary: {exc}",
            )
            source_cache[path] = result
            return result

        if not resolved.is_file():
            result = _SourceFailure(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                detail="Visible Git path is not a regular readable file in the current worktree.",
            )
            source_cache[path] = result
            return result

        try:
            size = resolved.stat().st_size
        except OSError as exc:
            result = _SourceFailure(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                detail=f"Visible file metadata could not be read: {exc}",
            )
            source_cache[path] = result
            return result
        if size > self._budget.max_source_file_bytes:
            result = _SourceFailure(
                reason=ContextTruncationReason.SOURCE_FILE_TOO_LARGE,
                detail=(
                    f"Source file size {size} bytes exceeds the inspection limit of "
                    f"{self._budget.max_source_file_bytes} bytes."
                ),
            )
            source_cache[path] = result
            return result

        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            result = _SourceFailure(
                reason=ContextTruncationReason.PATH_UNAVAILABLE,
                detail=f"Visible file bytes could not be read: {exc}",
            )
            source_cache[path] = result
            return result
        if len(raw) > self._budget.max_source_file_bytes:
            result = _SourceFailure(
                reason=ContextTruncationReason.SOURCE_FILE_TOO_LARGE,
                detail=(
                    f"Source file size {len(raw)} bytes exceeds the inspection limit of "
                    f"{self._budget.max_source_file_bytes} bytes."
                ),
            )
            source_cache[path] = result
            return result

        try:
            # Keep the byte hash below tied to the exact worktree content, but use
            # one canonical newline representation for model context and budgets.
            # Git worktrees on Windows may contain CRLF even when the same source
            # is LF-only on another supported host.
            text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            result = _SourceFailure(
                reason=ContextTruncationReason.NON_UTF8,
                detail="Visible file is not valid UTF-8 text and was omitted from model context.",
            )
            source_cache[path] = result
            return result

        result = _LoadedSource(raw=raw, text=text)
        source_cache[path] = result
        return result

    def _load_candidate(
        self,
        candidate: _Candidate,
        *,
        workspace: LocalGitWorkspace,
        source_cache: dict[str, _SourceResult],
        remaining_chars: int,
        remaining_tokens: int,
    ) -> tuple[ContextFile, list[ContextTruncation]] | ContextTruncation:
        source = self._read_source(
            candidate.path,
            workspace=workspace,
            source_cache=source_cache,
        )
        if isinstance(source, _SourceFailure):
            return ContextTruncation(
                reason=source.reason,
                path=candidate.path,
                detail=source.detail,
                omitted_files=1,
            )

        if candidate.regions:
            return self._load_region_candidate(
                candidate,
                source=source,
                remaining_chars=remaining_chars,
                remaining_tokens=remaining_tokens,
            )
        return self._load_prefix_candidate(
            candidate,
            source=source,
            remaining_chars=remaining_chars,
            remaining_tokens=remaining_tokens,
        )

    def _load_region_candidate(
        self,
        candidate: _Candidate,
        *,
        source: _LoadedSource,
        remaining_chars: int,
        remaining_tokens: int,
    ) -> tuple[ContextFile, list[ContextTruncation]] | ContextTruncation:
        lines = source.text.splitlines(keepends=True)
        snippets: list[ContextSnippet] = []
        reason_omissions: dict[ContextTruncationReason, int] = {}
        per_file_remaining = self._budget.max_chars_per_file
        total_remaining = remaining_chars
        token_remaining = remaining_tokens

        for region in candidate.regions:
            region_text = self._region_text(lines, region)
            if not region_text:
                continue
            char_limit = min(per_file_remaining, total_remaining)
            selected = self._fit_prefix(
                region_text,
                max_chars=char_limit,
                max_token_units=token_remaining,
            )
            omitted_chars = len(region_text) - len(selected)
            if omitted_chars:
                self._record_region_budget_limits(
                    reason_omissions,
                    region_text=region_text,
                    omitted_chars=omitted_chars,
                    per_file_remaining=per_file_remaining,
                    total_remaining=total_remaining,
                    token_remaining=token_remaining,
                )
            if not selected:
                continue

            line_count = max(1, len(selected.splitlines()))
            snippets.append(
                ContextSnippet(
                    start_line=region.start_line,
                    end_line=min(
                        region.end_line,
                        region.start_line + line_count - 1,
                    ),
                    content=selected,
                    char_count=len(selected),
                    estimated_tokens=self._token_estimator.context_window_units(selected),
                    billable_token_estimate=self._token_estimator.billable_token_estimate(selected),
                )
            )
            per_file_remaining -= len(selected)
            total_remaining -= len(selected)
            token_remaining -= self._token_estimator.context_window_units(selected)

        if not snippets:
            desired_chars = sum(
                len(self._region_text(lines, region)) for region in candidate.regions
            )
            return ContextTruncation(
                reason=ContextTruncationReason.TOKEN_BUDGET,
                path=candidate.path,
                detail=(
                    "No selected AST code region fit inside the remaining conservative context "
                    "budget."
                ),
                omitted_chars=desired_chars,
                omitted_files=1,
            )

        selected_chars = sum(snippet.char_count for snippet in snippets)
        selected_tokens = sum(snippet.estimated_tokens for snippet in snippets)
        billable_tokens = sum(snippet.billable_token_estimate for snippet in snippets)
        context_file = ContextFile(
            path=candidate.path,
            tracked=candidate.tracked,
            changed=candidate.changed,
            scope_matches=list(candidate.scope_matches),
            selection_reasons=list(candidate.selection_reasons),
            source_sha256=sha256(source.raw).hexdigest(),
            source_chars=len(source.text),
            source_bytes=len(source.raw),
            snippets=snippets,
            selected_chars=selected_chars,
            estimated_tokens=selected_tokens,
            billable_token_estimate=billable_tokens,
            truncated=bool(reason_omissions),
        )
        return context_file, self._region_truncations(candidate.path, reason_omissions)

    @staticmethod
    def _region_text(lines: list[str], region: RelevantCodeRegion) -> str:
        if region.start_line > len(lines):
            return ""
        end_index = min(region.end_line, len(lines))
        return "".join(lines[region.start_line - 1 : end_index])

    @staticmethod
    def _record_region_budget_limits(
        reason_omissions: dict[ContextTruncationReason, int],
        *,
        region_text: str,
        omitted_chars: int,
        per_file_remaining: int,
        total_remaining: int,
        token_remaining: int,
    ) -> None:
        if per_file_remaining < len(region_text):
            reason_omissions[ContextTruncationReason.PER_FILE_CHAR_LIMIT] = (
                reason_omissions.get(ContextTruncationReason.PER_FILE_CHAR_LIMIT, 0) + omitted_chars
            )
        if total_remaining < len(region_text):
            reason_omissions[ContextTruncationReason.TOTAL_CHAR_LIMIT] = (
                reason_omissions.get(ContextTruncationReason.TOTAL_CHAR_LIMIT, 0) + omitted_chars
            )
        if token_remaining < len(region_text.encode("utf-8")):
            reason_omissions[ContextTruncationReason.TOKEN_BUDGET] = (
                reason_omissions.get(ContextTruncationReason.TOKEN_BUDGET, 0) + omitted_chars
            )

    @staticmethod
    def _region_truncations(
        path: str,
        reason_omissions: dict[ContextTruncationReason, int],
    ) -> list[ContextTruncation]:
        details = {
            ContextTruncationReason.PER_FILE_CHAR_LIMIT: (
                "Selected AST code regions were truncated by the per-file character budget."
            ),
            ContextTruncationReason.TOTAL_CHAR_LIMIT: (
                "Selected AST code regions were truncated by the remaining total character budget."
            ),
            ContextTruncationReason.TOKEN_BUDGET: (
                "Selected AST code regions were truncated by the remaining conservative token "
                "budget."
            ),
        }
        ordered_reasons = (
            ContextTruncationReason.PER_FILE_CHAR_LIMIT,
            ContextTruncationReason.TOTAL_CHAR_LIMIT,
            ContextTruncationReason.TOKEN_BUDGET,
        )
        return [
            ContextTruncation(
                reason=reason,
                path=path,
                detail=details[reason],
                omitted_chars=reason_omissions[reason],
            )
            for reason in ordered_reasons
            if reason in reason_omissions
        ]

    def _load_prefix_candidate(
        self,
        candidate: _Candidate,
        *,
        source: _LoadedSource,
        remaining_chars: int,
        remaining_tokens: int,
    ) -> tuple[ContextFile, list[ContextTruncation]] | ContextTruncation:
        char_limit = min(self._budget.max_chars_per_file, remaining_chars)
        selected = self._fit_prefix(
            source.text,
            max_chars=char_limit,
            max_token_units=remaining_tokens,
        )
        if source.text and not selected:
            return ContextTruncation(
                reason=ContextTruncationReason.TOKEN_BUDGET,
                path=candidate.path,
                detail=(
                    "No complete UTF-8 character from this file fit inside the remaining "
                    "conservative token budget."
                ),
                omitted_chars=len(source.text),
                omitted_files=1,
            )

        selected_tokens = self._token_estimator.context_window_units(selected)
        line_count = max(1, len(selected.splitlines()))
        snippet = ContextSnippet(
            start_line=1,
            end_line=line_count,
            content=selected,
            char_count=len(selected),
            estimated_tokens=selected_tokens,
            billable_token_estimate=self._token_estimator.billable_token_estimate(selected),
        )
        truncated = len(selected) < len(source.text)
        context_file = ContextFile(
            path=candidate.path,
            tracked=candidate.tracked,
            changed=candidate.changed,
            scope_matches=list(candidate.scope_matches),
            selection_reasons=list(candidate.selection_reasons),
            source_sha256=sha256(source.raw).hexdigest(),
            source_chars=len(source.text),
            source_bytes=len(source.raw),
            snippets=[snippet],
            selected_chars=len(selected),
            estimated_tokens=selected_tokens,
            billable_token_estimate=snippet.billable_token_estimate,
            truncated=truncated,
        )

        file_truncations: list[ContextTruncation] = []
        if truncated:
            omitted_chars = len(source.text) - len(selected)
            if self._budget.max_chars_per_file < len(source.text):
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
            if remaining_chars < len(source.text):
                file_truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOTAL_CHAR_LIMIT,
                        path=candidate.path,
                        detail=(
                            "File content was truncated by the remaining total character budget."
                        ),
                        omitted_chars=omitted_chars,
                    )
                )
            if remaining_tokens < len(source.raw):
                file_truncations.append(
                    ContextTruncation(
                        reason=ContextTruncationReason.TOKEN_BUDGET,
                        path=candidate.path,
                        detail=(
                            "File content was truncated by the remaining conservative token budget."
                        ),
                        omitted_chars=omitted_chars,
                    )
                )

        return context_file, file_truncations

    def _fit_prefix(self, text: str, *, max_chars: int, max_token_units: int) -> str:
        if not text or max_chars <= 0 or max_token_units <= 0:
            return ""
        candidate = text[:max_chars]
        if self._token_estimator.context_window_units(candidate) <= max_token_units:
            return candidate

        low = 0
        high = len(candidate)
        while low < high:
            midpoint = (low + high + 1) // 2
            if self._token_estimator.context_window_units(candidate[:midpoint]) <= max_token_units:
                low = midpoint
            else:
                high = midpoint - 1
        return candidate[:low]
