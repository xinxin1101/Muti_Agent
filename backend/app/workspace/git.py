from __future__ import annotations

import subprocess
from collections.abc import Sequence
from difflib import unified_diff
from pathlib import Path, PurePosixPath


class WorkspaceGitError(RuntimeError):
    """Raised when the managed local repository cannot provide trustworthy Git state."""


class LocalGitWorkspace:
    """Managed local Git repository used as the source of truth for repository changes."""

    def __init__(self, root: str | Path, *, git_timeout_seconds: float = 10.0) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")

        resolved_root = Path(root).expanduser().resolve()
        if not resolved_root.is_dir():
            raise ValueError("workspace root must be an existing directory")

        self._root = resolved_root
        self._git_timeout_seconds = git_timeout_seconds
        self._assert_repository()
        self._assert_head()

    @property
    def root(self) -> Path:
        return self._root

    def resolve_path(self, repository_path: str) -> Path:
        """Resolve one repository-relative POSIX path without allowing workspace escape."""

        normalized = repository_path.strip()
        if not normalized:
            raise ValueError("repository path must not be empty")
        if "\\" in normalized:
            raise ValueError("repository path must use POSIX-style '/' separators")
        if len(normalized) >= 2 and normalized[1] == ":":
            raise ValueError("repository path must not contain a Windows drive prefix")

        pure_path = PurePosixPath(normalized)
        if pure_path.is_absolute():
            raise ValueError("repository path must be relative")
        if normalized == "." or any(part == ".." for part in pure_path.parts):
            raise ValueError("repository path must remain inside the workspace")
        if pure_path.parts and pure_path.parts[0] == ".git":
            raise ValueError("repository path must not access .git internals")

        current = self._root
        for part in pure_path.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    "repository path resolves outside the trusted path boundary or traverses "
                    "symbolic links"
                )

        candidate = current.resolve(strict=False)
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("repository path resolves outside the workspace") from exc
        return candidate

    def head_commit(self) -> str:
        """Return the exact Git HEAD commit for provenance."""

        commit = self._git(["rev-parse", "--verify", "HEAD"]).strip()
        if not commit:
            raise WorkspaceGitError("workspace repository does not have a resolvable HEAD commit")
        return commit

    def tracked_files(self) -> list[str]:
        """Return sorted tracked repository paths without exposing `.git` internals."""

        return sorted(set(self._split_nul(self._git(["ls-files", "-z", "--cached", "--"]))))

    def repository_files(self) -> list[str]:
        """Return sorted tracked plus non-ignored untracked paths from current worktree truth."""

        tracked = self.tracked_files()
        untracked = self._split_nul(
            self._git(["ls-files", "--others", "--exclude-standard", "-z", "--"])
        )
        return sorted(set(tracked) | set(untracked))

    def changed_files(self) -> list[str]:
        """Return tracked and untracked changed paths relative to HEAD.

        Rename detection is disabled intentionally so moving a protected file is represented as
        deletion of the protected path plus addition of the destination path. This prevents a
        rename from bypassing read-only scope enforcement.
        """

        tracked = self._split_nul(
            self._git(["diff", "--no-renames", "--name-only", "-z", "HEAD", "--"])
        )
        untracked = self._split_nul(
            self._git(["ls-files", "--others", "--exclude-standard", "-z", "--"])
        )
        return sorted(set(tracked) | set(untracked))

    def unified_diff(self) -> str:
        """Return reviewable HEAD-to-workspace evidence, including untracked text files."""

        tracked_diff = self._git(
            [
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--unified=3",
                "HEAD",
                "--",
            ]
        )
        untracked = self._split_nul(
            self._git(["ls-files", "--others", "--exclude-standard", "-z", "--"])
        )

        sections = [tracked_diff] if tracked_diff else []
        for repository_path in sorted(untracked):
            path = self.resolve_path(repository_path)
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                sections.append(f"Binary untracked file omitted: {repository_path}\n")
                continue

            patch = "".join(
                unified_diff(
                    [],
                    content.splitlines(keepends=True),
                    fromfile="/dev/null",
                    tofile=f"b/{repository_path}",
                    lineterm="\n",
                )
            )
            if patch:
                sections.append(patch)

        return "\n".join(section.rstrip("\n") for section in sections if section) + (
            "\n" if sections else ""
        )

    def _assert_repository(self) -> None:
        result = self._git(["rev-parse", "--is-inside-work-tree"], check=False)
        if result.strip() != "true":
            raise WorkspaceGitError("workspace root is not inside a Git working tree")

        top_level = self._git(["rev-parse", "--show-toplevel"], check=False).strip()
        if not top_level or Path(top_level).resolve() != self._root:
            raise WorkspaceGitError("workspace root must be the Git repository top level")

    def _assert_head(self) -> None:
        result = self._git(["rev-parse", "--verify", "HEAD"], check=False)
        if not result.strip():
            raise WorkspaceGitError("workspace repository must have a valid HEAD commit")

    def _git(self, arguments: Sequence[str], *, check: bool = True) -> str:
        command = ["git", "-C", str(self._root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorkspaceGitError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceGitError("git command exceeded the workspace timeout") from exc

        if check and completed.returncode != 0:
            stderr = completed.stderr.strip() or "unknown git error"
            raise WorkspaceGitError(f"git command failed: {stderr}")
        return completed.stdout

    @staticmethod
    def _split_nul(value: str) -> list[str]:
        return [item for item in value.split("\0") if item]
