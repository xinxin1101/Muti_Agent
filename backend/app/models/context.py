from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContextScopeKind(StrEnum):
    READABLE = "readable"
    WRITABLE = "writable"
    READ_ONLY = "read_only"


class ContextSelectionReason(StrEnum):
    CHANGED = "changed"
    WRITABLE_SCOPE = "writable_scope"
    READ_ONLY_SCOPE = "read_only_scope"
    READABLE_SCOPE = "readable_scope"


class ContextTruncationReason(StrEnum):
    PER_FILE_CHAR_LIMIT = "PER_FILE_CHAR_LIMIT"
    TOTAL_CHAR_LIMIT = "TOTAL_CHAR_LIMIT"
    TOKEN_BUDGET = "TOKEN_BUDGET"
    FILE_COUNT_LIMIT = "FILE_COUNT_LIMIT"
    SOURCE_FILE_TOO_LARGE = "SOURCE_FILE_TOO_LARGE"
    NON_UTF8 = "NON_UTF8"
    PATH_UNAVAILABLE = "PATH_UNAVAILABLE"


def _normalize_repository_path(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("repository path must not be empty")
    if normalized.startswith(("/", "\\")) or "\\" in normalized:
        raise ValueError("repository path must be repository-relative POSIX style")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise ValueError("repository path must not use a Windows drive prefix")
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError("repository path must remain inside the repository")
    if normalized == ".git" or normalized.startswith(".git/"):
        raise ValueError("repository path must not expose .git internals")
    return normalized


def _canonical_fingerprint(payload: dict[str, object]) -> str:
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class ContextScopeMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContextScopeKind
    pattern: str = Field(min_length=1, max_length=500)


class ContextSnippet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content: str
    char_count: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_evidence(self) -> ContextSnippet:
        if self.end_line < self.start_line:
            raise ValueError("snippet end_line must be greater than or equal to start_line")
        if self.char_count != len(self.content):
            raise ValueError("snippet char_count must equal the selected content length")
        if self.estimated_tokens != len(self.content.encode("utf-8")):
            raise ValueError(
                "snippet estimated_tokens must use the utf8_bytes_upper_bound estimator"
            )
        return self


class ContextFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1000)
    tracked: bool
    changed: bool
    scope_matches: list[ContextScopeMatch] = Field(min_length=1)
    selection_reasons: list[ContextSelectionReason] = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_chars: int = Field(ge=0)
    source_bytes: int = Field(ge=0)
    snippets: list[ContextSnippet] = Field(min_length=1)
    selected_chars: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated: bool = False

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_repository_path(value)

    @model_validator(mode="after")
    def validate_aggregate_usage(self) -> ContextFile:
        if self.selected_chars != sum(snippet.char_count for snippet in self.snippets):
            raise ValueError("selected_chars must equal the sum of snippet char counts")
        if self.estimated_tokens != sum(snippet.estimated_tokens for snippet in self.snippets):
            raise ValueError("estimated_tokens must equal the sum of snippet token estimates")
        if self.selected_chars > self.source_chars:
            raise ValueError("selected_chars cannot exceed source_chars")
        if self.estimated_tokens > self.source_bytes:
            raise ValueError("selected token estimate cannot exceed source bytes")
        return self


class ContextTruncation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: ContextTruncationReason
    path: str | None = Field(default=None, max_length=1000)
    detail: str = Field(min_length=1, max_length=2000)
    omitted_chars: int = Field(default=0, ge=0)
    omitted_files: int = Field(default=0, ge=0)

    @field_validator("path")
    @classmethod
    def validate_optional_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_repository_path(value)


class ContextBudget(BaseModel):
    """Provider-neutral repository-content budget for one ContextPacket."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_files: int = Field(default=12, ge=1, le=100)
    max_chars_per_file: int = Field(default=8_000, ge=100, le=100_000)
    max_total_chars: int = Field(default=24_000, ge=100, le=500_000)
    max_estimated_tokens: int = Field(default=32_000, ge=100, le=500_000)
    max_source_file_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)


class ContextUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_files: int = Field(ge=0)
    selected_files: int = Field(ge=0)
    selected_chars: int = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    truncated_files: int = Field(ge=0)
    omitted_files: int = Field(ge=0)
    reused_files: int = Field(default=0, ge=0)
    trimmed_files: int = Field(default=0, ge=0)
    prompt_estimated_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_prompt_estimate(self) -> ContextUsage:
        if self.prompt_estimated_tokens and self.prompt_estimated_tokens < self.estimated_tokens:
            raise ValueError("prompt token estimate cannot be smaller than selected content")
        return self


class ContextFileDigest(BaseModel):
    """Content identity retained for a later compact continuation, never source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1000)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _normalize_repository_path(value)


class ContextContinuationState(BaseModel):
    """Bounded, content-free state used to resume a task without replaying source files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary_version: str = Field(min_length=1, max_length=64)
    repository_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    read_files: tuple[ContextFileDigest, ...] = Field(default_factory=tuple, max_length=100)
    changed_files: tuple[str, ...] = Field(default_factory=tuple, max_length=512)
    completed_summary: str = Field(default="", max_length=512)
    remaining_summary: str = Field(default="", max_length=512)
    verification_summary: str = Field(default="", max_length=512)
    failure_summary: str = Field(default="", max_length=512)

    @field_validator("changed_files")
    @classmethod
    def validate_changed_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_repository_path(value) for value in values)
        if normalized != tuple(sorted(set(normalized))):
            raise ValueError("continuation changed_files must be sorted and unique")
        return normalized

    @model_validator(mode="after")
    def validate_unique_read_files(self) -> ContextContinuationState:
        paths = [item.path for item in self.read_files]
        if len(paths) != len(set(paths)):
            raise ValueError("continuation read_files must not contain duplicate paths")
        return self


class ContextPacket(BaseModel):
    """Bounded repository context compiled from one trusted worktree state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    objective: str = Field(min_length=1, max_length=4000)
    acceptance_criteria: list[str] = Field(min_length=1)
    readable_files: list[str] = Field(default_factory=list)
    writable_files: list[str] = Field(min_length=1)
    readonly_files: list[str] = Field(default_factory=list)
    repository_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    repository_summary_version: str = Field(
        default="repository_summary_v1",
        min_length=1,
        max_length=64,
    )
    repository_summary: str = Field(default="", max_length=16_000)
    resume: ContextContinuationState | None = None
    changed_files: list[str] = Field(default_factory=list)
    repository_map: list[str] = Field(default_factory=list, max_length=80)
    selected_files: list[ContextFile] = Field(default_factory=list)
    truncations: list[ContextTruncation] = Field(default_factory=list)
    budget: ContextBudget
    usage: ContextUsage
    selection_strategy: str = "changed>writable>read_only>readable>path"
    snippet_strategy: str = "deterministic_prefix"
    token_estimator: str = "utf8_bytes_upper_bound"
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("changed_files")
    @classmethod
    def validate_changed_paths(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_repository_path(value) for value in values]
        if normalized != sorted(set(normalized)):
            raise ValueError("changed_files must be sorted and unique")
        return normalized

    @field_validator("repository_map")
    @classmethod
    def validate_repository_map(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_repository_path(value) for value in values]
        if normalized != sorted(set(normalized)):
            raise ValueError("repository_map must be sorted and unique")
        return normalized

    @model_validator(mode="after")
    def validate_packet_usage_and_fingerprint(self) -> ContextPacket:
        paths = [item.path for item in self.selected_files]
        if len(paths) != len(set(paths)):
            raise ValueError("selected_files must not contain duplicate paths")
        if self.usage.selected_files != len(self.selected_files):
            raise ValueError("usage.selected_files must match selected_files length")
        if self.usage.selected_chars != sum(item.selected_chars for item in self.selected_files):
            raise ValueError("usage.selected_chars must match selected file usage")
        if self.usage.estimated_tokens != sum(
            item.estimated_tokens for item in self.selected_files
        ):
            raise ValueError("usage.estimated_tokens must match selected file usage")
        if self.usage.selected_chars > self.budget.max_total_chars:
            raise ValueError("packet exceeds max_total_chars")
        if self.usage.estimated_tokens > self.budget.max_estimated_tokens:
            raise ValueError("packet exceeds max_estimated_tokens")
        if self.usage.selected_files > self.budget.max_files:
            raise ValueError("packet exceeds max_files")
        if self.usage.trimmed_files != self.usage.truncated_files:
            raise ValueError("trimmed_files must match truncated_files")

        canonical_payload = self.model_dump(mode="json", exclude={"fingerprint"})
        expected_fingerprint = _canonical_fingerprint(canonical_payload)
        if self.fingerprint != expected_fingerprint:
            raise ValueError("ContextPacket fingerprint does not match its canonical payload")
        return self
