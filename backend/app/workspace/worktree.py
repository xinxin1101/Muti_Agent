from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.workspace.git import LocalGitWorkspace, WorkspaceGitError

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_BRANCH_NAMESPACE = "devflow/task"


class TaskWorktreeError(WorkspaceGitError):
    """Base error for task-worktree lifecycle failures."""


class TaskWorktreeCollisionError(TaskWorktreeError):
    """Raised when a task worktree path, branch, or registration already exists."""


class StaleTaskWorktreeError(TaskWorktreeError):
    """Raised when Git still records a task worktree whose directory is missing."""


@dataclass(frozen=True, slots=True)
class TaskWorktreeRecord:
    """Immutable evidence describing one linked worktree created for a task."""

    task_id: str
    base_commit: str
    branch_name: str
    path: Path


@dataclass(frozen=True, slots=True)
class _RegisteredWorktree:
    path: Path
    head: str | None = None
    branch_ref: str | None = None
    locked_reason: str | None = None
    prunable_reason: str | None = None


class TaskWorktreeManager:
    """Create and safely remove one isolated Git linked worktree per task."""

    def __init__(
        self,
        base_workspace: LocalGitWorkspace,
        worktree_root: str | Path,
        *,
        git_timeout_seconds: float = 10.0,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if base_workspace.changed_files():
            raise TaskWorktreeError("base workspace must be clean before freezing a worktree base")

        root = Path(worktree_root).expanduser()
        if root.exists() and root.is_symlink():
            raise ValueError("worktree root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
        try:
            resolved_root.relative_to(base_workspace.root)
        except ValueError:
            pass
        else:
            raise ValueError("worktree root must be outside the base repository")

        self._base_workspace = base_workspace
        self._root = resolved_root
        self._git_timeout_seconds = git_timeout_seconds
        self._base_commit = self._git(["rev-parse", "--verify", "HEAD^{commit}"]).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(self._base_commit) is None:
            raise TaskWorktreeError("Git did not return a full immutable base commit id")

    @property
    def base_commit(self) -> str:
        return self._base_commit

    @property
    def root(self) -> Path:
        return self._root

    def record_for(self, task_id: str) -> TaskWorktreeRecord:
        """Return the deterministic branch/path identity reserved for one task."""

        normalized = self._validate_task_id(task_id)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized).strip("-_").lower()
        slug = (slug or "task")[:48]
        branch_name = f"{_BRANCH_NAMESPACE}/{slug}-{digest}"
        path = self._root / f"{slug}-{digest}"
        self._validate_branch_name(branch_name)
        return TaskWorktreeRecord(
            task_id=normalized,
            base_commit=self._base_commit,
            branch_name=branch_name,
            path=path,
        )

    def create(self, task_id: str) -> TaskWorktreeRecord:
        """Create a locked linked worktree and a fresh task branch from the frozen base commit."""

        if self._base_workspace.changed_files():
            raise TaskWorktreeError("base workspace became dirty after the worktree base was frozen")

        record = self.record_for(task_id)
        registered = self._registered_worktrees()
        existing = self._find_registered_path(record.path, registered)
        if existing is not None:
            if not record.path.exists() or existing.prunable_reason is not None:
                raise StaleTaskWorktreeError(
                    f"stale Git worktree registration exists for task {record.task_id}"
                )
            raise TaskWorktreeCollisionError(
                f"task worktree is already registered at {record.path}"
            )
        if record.path.exists():
            raise TaskWorktreeCollisionError(
                f"task worktree path already exists but is not registered: {record.path}"
            )
        if self._branch_exists(record.branch_name):
            raise TaskWorktreeCollisionError(
                f"task branch already exists: {record.branch_name}"
            )

        self._git(
            [
                "worktree",
                "add",
                "--lock",
                "--reason",
                f"DevFlow task {record.task_id}",
                "-b",
                record.branch_name,
                str(record.path),
                record.base_commit,
            ]
        )
        self._verify_created(record)
        return record

    def open_workspace(self, task_id: str) -> LocalGitWorkspace:
        """Open an existing manager-owned task worktree as a normal LocalGitWorkspace."""

        record = self.record_for(task_id)
        entry = self._find_registered_path(record.path, self._registered_worktrees())
        if entry is None:
            raise TaskWorktreeError(f"task worktree is not registered: {record.task_id}")
        self._assert_owned_registration(record, entry)
        if not record.path.exists():
            raise StaleTaskWorktreeError(
                f"registered task worktree directory is missing: {record.path}"
            )
        return LocalGitWorkspace(
            record.path,
            git_timeout_seconds=self._git_timeout_seconds,
        )

    def remove(self, task_id: str, *, force: bool = False) -> bool:
        """Remove one manager-owned linked worktree while deliberately preserving its branch."""

        record = self.record_for(task_id)
        registered = self._registered_worktrees()
        entry = self._find_registered_path(record.path, registered)
        if entry is None:
            if record.path.exists():
                raise TaskWorktreeCollisionError(
                    f"unregistered path exists at the task worktree location: {record.path}"
                )
            return False

        self._assert_owned_registration(record, entry)
        if not record.path.exists() or entry.prunable_reason is not None:
            raise StaleTaskWorktreeError(
                f"cannot safely remove stale task worktree registration: {record.task_id}"
            )

        workspace = LocalGitWorkspace(
            record.path,
            git_timeout_seconds=self._git_timeout_seconds,
        )
        changed = workspace.changed_files()
        if changed and not force:
            raise TaskWorktreeError(
                "refusing to remove dirty task worktree without force=True: "
                + ", ".join(changed)
            )

        was_locked = entry.locked_reason is not None
        if was_locked:
            self._git(["worktree", "unlock", str(record.path)])
        try:
            arguments = ["worktree", "remove"]
            if force:
                arguments.append("--force")
            arguments.append(str(record.path))
            self._git(arguments)
        except Exception:
            if was_locked and record.path.exists():
                self._git(
                    [
                        "worktree",
                        "lock",
                        "--reason",
                        f"DevFlow task {record.task_id}",
                        str(record.path),
                    ],
                    check=False,
                )
            raise

        if record.path.exists():
            raise TaskWorktreeError("Git reported removal success but the worktree path still exists")
        if self._find_registered_path(record.path, self._registered_worktrees()) is not None:
            raise TaskWorktreeError("Git reported removal success but worktree metadata still exists")
        return True

    def _verify_created(self, record: TaskWorktreeRecord) -> None:
        entry = self._find_registered_path(record.path, self._registered_worktrees())
        if entry is None:
            raise TaskWorktreeError("created worktree is missing from Git worktree metadata")
        self._assert_owned_registration(record, entry)
        if entry.locked_reason is None:
            raise TaskWorktreeError("created task worktree must remain locked")

        workspace = LocalGitWorkspace(
            record.path,
            git_timeout_seconds=self._git_timeout_seconds,
        )
        if workspace.changed_files():
            raise TaskWorktreeError("new task worktree must start clean")

        head = self._git_at(record.path, ["rev-parse", "--verify", "HEAD^{commit}"]).stdout.strip()
        if head != record.base_commit:
            raise TaskWorktreeError("new task worktree does not point at the frozen base commit")
        branch = self._git_at(
            record.path,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
        ).stdout.strip()
        if branch != record.branch_name:
            raise TaskWorktreeError("new task worktree is not checked out on its reserved task branch")

    def _assert_owned_registration(
        self,
        record: TaskWorktreeRecord,
        entry: _RegisteredWorktree,
    ) -> None:
        expected_ref = f"refs/heads/{record.branch_name}"
        if entry.branch_ref != expected_ref:
            raise TaskWorktreeCollisionError(
                "worktree path is registered to an unexpected branch: "
                f"expected {expected_ref}, got {entry.branch_ref or '<detached>'}"
            )

    def _registered_worktrees(self) -> tuple[_RegisteredWorktree, ...]:
        raw = self._git(["worktree", "list", "--porcelain", "-z"]).stdout
        records: list[_RegisteredWorktree] = []
        current: dict[str, str] = {}

        def flush() -> None:
            if "worktree" not in current:
                current.clear()
                return
            records.append(
                _RegisteredWorktree(
                    path=Path(current["worktree"]).resolve(),
                    head=current.get("HEAD"),
                    branch_ref=current.get("branch"),
                    locked_reason=current.get("locked"),
                    prunable_reason=current.get("prunable"),
                )
            )
            current.clear()

        for field in raw.split("\0"):
            if not field:
                continue
            key, separator, value = field.partition(" ")
            if key == "worktree" and "worktree" in current:
                flush()
            current[key] = value if separator else ""
        flush()
        return tuple(records)

    @staticmethod
    def _find_registered_path(
        path: Path,
        entries: tuple[_RegisteredWorktree, ...],
    ) -> _RegisteredWorktree | None:
        resolved = path.resolve(strict=False)
        return next((entry for entry in entries if entry.path == resolved), None)

    def _branch_exists(self, branch_name: str) -> bool:
        result = self._git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            check=False,
        )
        return result.returncode == 0

    def _validate_branch_name(self, branch_name: str) -> None:
        result = self._git(["check-ref-format", "--branch", branch_name], check=False)
        if result.returncode != 0:
            raise TaskWorktreeError(f"generated invalid Git task branch name: {branch_name}")

    @staticmethod
    def _validate_task_id(task_id: str) -> str:
        normalized = task_id.strip()
        if (
            not normalized
            or len(normalized) > 128
            or _TASK_ID_PATTERN.fullmatch(normalized) is None
        ):
            raise ValueError("task_id must use the TaskContract task_id format")
        return normalized

    def _git(
        self,
        arguments: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return self._git_at(self._base_workspace.root, arguments, check=check)

    def _git_at(
        self,
        root: Path,
        arguments: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise TaskWorktreeError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise TaskWorktreeError("git worktree command exceeded the configured timeout") from exc

        if check and completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise TaskWorktreeError(f"git worktree command failed: {stderr}")
        return completed


def remove_unregistered_empty_directory(path: str | Path) -> None:
    """Remove only an empty, non-symlink directory; useful after failed external setup."""

    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError("refusing to remove a symbolic-link directory")
    if not candidate.exists():
        return
    if not candidate.is_dir() or any(candidate.iterdir()):
        raise ValueError("refusing to remove a non-empty or non-directory path")
    shutil.rmtree(candidate)
