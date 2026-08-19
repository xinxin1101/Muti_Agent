from __future__ import annotations

import hashlib
import re
import subprocess
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.workspace.git import LocalGitWorkspace

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")

DEFAULT_MAX_DIFF_FILES = 100
DEFAULT_MAX_FILE_PATCH_BYTES = 64 * 1024
DEFAULT_MAX_TOTAL_PATCH_BYTES = 256 * 1024
DEFAULT_MAX_DIFF_BLOB_BYTES = 512 * 1024


class CommitDiffError(RuntimeError):
    """Raised when Git cannot provide a trustworthy bounded commit diff."""


class CommitDiffFileStatus(StrEnum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    TYPE_CHANGED = "TYPE_CHANGED"


class CommitDiffOmissionReason(StrEnum):
    BINARY = "BINARY"
    BLOB_LIMIT = "BLOB_LIMIT"
    TOTAL_PATCH_LIMIT = "TOTAL_PATCH_LIMIT"


class CommitDiffFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    status: CommitDiffFileStatus
    additions: int | None = Field(default=None, ge=0)
    deletions: int | None = Field(default=None, ge=0)
    binary: bool = False
    patch: str | None = None
    patch_bytes: int = Field(default=0, ge=0)
    patch_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    patch_truncated: bool = False
    patch_omitted_reason: CommitDiffOmissionReason | None = None

    @model_validator(mode="after")
    def validate_patch_shape(self) -> CommitDiffFile:
        if self.binary and (self.additions is not None or self.deletions is not None):
            raise ValueError("binary diff entries must not claim text line counts")
        if self.patch is None:
            if self.patch_bytes != 0 or self.patch_sha256 is not None:
                raise ValueError("omitted patches must not carry patch bytes or hash")
            if self.patch_omitted_reason is None:
                raise ValueError("omitted patches require an omission reason")
        elif self.patch_omitted_reason is not None:
            raise ValueError("rendered patches must not carry an omission reason")
        return self


class CommitDiffSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changed_file_count: int = Field(ge=0)
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    files: tuple[CommitDiffFile, ...]
    omitted_file_count: int = Field(ge=0)
    patch_bytes: int = Field(ge=0)
    truncated: bool

    @model_validator(mode="after")
    def validate_counts(self) -> CommitDiffSnapshot:
        if self.changed_file_count != len(self.files) + self.omitted_file_count:
            raise ValueError("diff file counts must match rendered plus omitted files")
        if self.truncated != (
            self.omitted_file_count > 0
            or any(item.patch_truncated for item in self.files)
        ):
            raise ValueError("diff truncated flag must match bounded output")
        return self


class ReadOnlyCommitDiffReader:
    """Read immutable commit-to-commit Git evidence without mutating refs or worktrees."""

    def __init__(
        self,
        workspace: LocalGitWorkspace,
        *,
        git_timeout_seconds: float = 10.0,
        max_files: int = DEFAULT_MAX_DIFF_FILES,
        max_file_patch_bytes: int = DEFAULT_MAX_FILE_PATCH_BYTES,
        max_total_patch_bytes: int = DEFAULT_MAX_TOTAL_PATCH_BYTES,
        max_blob_bytes: int = DEFAULT_MAX_DIFF_BLOB_BYTES,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if min(max_files, max_file_patch_bytes, max_total_patch_bytes, max_blob_bytes) <= 0:
            raise ValueError("diff bounds must be greater than zero")
        self._root = workspace.root
        self._git_timeout_seconds = git_timeout_seconds
        self._max_files = max_files
        self._max_file_patch_bytes = max_file_patch_bytes
        self._max_total_patch_bytes = max_total_patch_bytes
        self._max_blob_bytes = max_blob_bytes

    def commit_parents(self, commit: str) -> tuple[str, ...]:
        resolved = self._resolve_commit(commit)
        value = self._git_text(["rev-list", "--parents", "-n", "1", resolved]).strip()
        parts = value.split()
        if not parts or parts[0] != resolved:
            raise CommitDiffError("Git returned an unexpected commit parent record")
        return tuple(parts[1:])

    def read(self, *, base_commit: str, head_commit: str) -> CommitDiffSnapshot:
        base = self._resolve_commit(base_commit)
        head = self._resolve_commit(head_commit)
        statuses = self._name_status(base, head)
        numstats = self._numstat(base, head)
        if set(statuses) != set(numstats):
            raise CommitDiffError("Git name-status and numstat paths disagree")

        all_paths = sorted(statuses)
        additions = sum(item[0] or 0 for item in numstats.values())
        deletions = sum(item[1] or 0 for item in numstats.values())
        selected_paths = all_paths[: self._max_files]
        omitted_file_count = len(all_paths) - len(selected_paths)
        remaining_patch_bytes = self._max_total_patch_bytes
        rendered: list[CommitDiffFile] = []

        for path in selected_paths:
            file_additions, file_deletions, binary = numstats[path]
            status = statuses[path]
            patch: str | None = None
            patch_bytes = 0
            patch_sha256: str | None = None
            patch_truncated = False
            omission: CommitDiffOmissionReason | None = None

            if binary:
                omission = CommitDiffOmissionReason.BINARY
            elif remaining_patch_bytes <= 0:
                omission = CommitDiffOmissionReason.TOTAL_PATCH_LIMIT
                patch_truncated = True
            elif self._blob_too_large(base, head, path):
                omission = CommitDiffOmissionReason.BLOB_LIMIT
                patch_truncated = True
            else:
                full_patch = self._git_bytes(
                    [
                        "diff",
                        "--no-ext-diff",
                        "--no-renames",
                        "--no-color",
                        "--unified=3",
                        base,
                        head,
                        "--",
                        path,
                    ]
                )
                allowed = min(self._max_file_patch_bytes, remaining_patch_bytes)
                displayed = full_patch[:allowed]
                patch_truncated = len(full_patch) > allowed
                patch = displayed.decode("utf-8", errors="replace")
                patch_bytes = len(displayed)
                patch_sha256 = hashlib.sha256(full_patch).hexdigest()
                remaining_patch_bytes -= patch_bytes

            rendered.append(
                CommitDiffFile(
                    path=path,
                    status=status,
                    additions=file_additions,
                    deletions=file_deletions,
                    binary=binary,
                    patch=patch,
                    patch_bytes=patch_bytes,
                    patch_sha256=patch_sha256,
                    patch_truncated=patch_truncated,
                    patch_omitted_reason=omission,
                )
            )

        patch_bytes_total = sum(item.patch_bytes for item in rendered)
        return CommitDiffSnapshot(
            base_commit=base,
            head_commit=head,
            changed_file_count=len(all_paths),
            additions=additions,
            deletions=deletions,
            files=tuple(rendered),
            omitted_file_count=omitted_file_count,
            patch_bytes=patch_bytes_total,
            truncated=omitted_file_count > 0 or any(item.patch_truncated for item in rendered),
        )

    def _name_status(self, base: str, head: str) -> dict[str, CommitDiffFileStatus]:
        value = self._git_text(
            ["diff", "--no-ext-diff", "--no-renames", "--name-status", "-z", base, head, "--"]
        )
        tokens = [item for item in value.split("\0") if item]
        if len(tokens) % 2 != 0:
            raise CommitDiffError("Git returned malformed name-status output")
        mapping = {
            "A": CommitDiffFileStatus.ADDED,
            "M": CommitDiffFileStatus.MODIFIED,
            "D": CommitDiffFileStatus.DELETED,
            "T": CommitDiffFileStatus.TYPE_CHANGED,
        }
        result: dict[str, CommitDiffFileStatus] = {}
        for index in range(0, len(tokens), 2):
            raw_status = tokens[index]
            path = tokens[index + 1]
            if raw_status not in mapping:
                raise CommitDiffError(f"unsupported Git diff status: {raw_status}")
            if not path or path.startswith(".git/") or path == ".git":
                raise CommitDiffError("Git diff returned an invalid repository path")
            if path in result:
                raise CommitDiffError("Git diff returned a duplicate repository path")
            result[path] = mapping[raw_status]
        return result

    def _numstat(self, base: str, head: str) -> dict[str, tuple[int | None, int | None, bool]]:
        value = self._git_text(
            ["diff", "--no-ext-diff", "--no-renames", "--numstat", "-z", base, head, "--"]
        )
        result: dict[str, tuple[int | None, int | None, bool]] = {}
        for record in (item for item in value.split("\0") if item):
            parts = record.split("\t", 2)
            if len(parts) != 3:
                raise CommitDiffError("Git returned malformed numstat output")
            raw_additions, raw_deletions, path = parts
            if path in result:
                raise CommitDiffError("Git numstat returned a duplicate repository path")
            binary = raw_additions == "-" and raw_deletions == "-"
            if binary:
                result[path] = (None, None, True)
                continue
            try:
                result[path] = (int(raw_additions), int(raw_deletions), False)
            except ValueError as exc:
                raise CommitDiffError("Git returned non-numeric text diff statistics") from exc
        return result

    def _blob_too_large(self, base: str, head: str, path: str) -> bool:
        sizes = [self._blob_size(base, path), self._blob_size(head, path)]
        return any(size is not None and size > self._max_blob_bytes for size in sizes)

    def _blob_size(self, commit: str, path: str) -> int | None:
        completed = self._run_text(["cat-file", "-s", f"{commit}:{path}"], check=False)
        if completed.returncode != 0:
            return None
        try:
            return int(completed.stdout.strip())
        except ValueError as exc:
            raise CommitDiffError("Git returned an invalid blob size") from exc

    def _resolve_commit(self, commit: str) -> str:
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            raise CommitDiffError("commit evidence must be a full lowercase Git object id")
        resolved = self._git_text(
            ["rev-parse", "--verify", f"{commit}^{{commit}}"],
            check=False,
        ).strip()
        if resolved != commit:
            raise CommitDiffError(
                "persisted commit evidence does not resolve to the expected commit"
            )
        return resolved

    def _git_text(self, arguments: list[str], *, check: bool = True) -> str:
        return self._run_text(arguments, check=check).stdout

    def _run_text(
        self,
        arguments: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(self._root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommitDiffError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommitDiffError("git diff command exceeded the read-only timeout") from exc
        if check and completed.returncode != 0:
            raise CommitDiffError(
                "read-only git command failed: " + (completed.stderr.strip() or "unknown git error")
            )
        return completed

    def _git_bytes(self, arguments: list[str]) -> bytes:
        command = ["git", "-C", str(self._root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommitDiffError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommitDiffError("git diff command exceeded the read-only timeout") from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise CommitDiffError("read-only git diff failed: " + (stderr or "unknown git error"))
        return completed.stdout
