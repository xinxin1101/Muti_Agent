from __future__ import annotations

import hashlib
import os
import re
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
    """Create, finalize, and safely remove one isolated Git linked worktree per task."""

    def __init__(
        self,
        base_workspace: LocalGitWorkspace,
        worktree_root: str | Path,
        *,
        git_timeout_seconds: float = 10.0,
        frozen_base_commit: str | None = None,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if base_workspace.changed_files():
            raise TaskWorktreeError("base workspace must be clean before freezing a worktree base")

        root = Path(worktree_root).expanduser()
        if root.exists() and root.is_symlink():
            raise ValueError("worktree root must not be a symbolic link")
        resolved_root = root.resolve(strict=False)
        try:
            resolved_root.relative_to(base_workspace.root)
        except ValueError:
            pass
        else:
            raise ValueError("worktree root must be outside the base repository")
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()

        self._base_workspace = base_workspace
        self._root = resolved_root
        self._git_timeout_seconds = git_timeout_seconds
        current_head = self._git(["rev-parse", "--verify", "HEAD^{commit}"]).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(current_head) is None:
            raise TaskWorktreeError("Git did not return a full immutable base commit id")
        self._base_commit = self._freeze_base_commit(
            current_head=current_head,
            requested=frozen_base_commit,
        )

    @property
    def base_commit(self) -> str:
        return self._base_commit

    @property
    def root(self) -> Path:
        return self._root

    def record_for(
        self,
        task_id: str,
        *,
        base_commit: str | None = None,
    ) -> TaskWorktreeRecord:
        """Return the deterministic branch/path identity reserved for one task."""

        normalized = self._validate_task_id(task_id)
        task_base = self._resolve_task_base(base_commit)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", normalized).strip("-_").lower()
        slug = (slug or "task")[:48]
        branch_name = f"{_BRANCH_NAMESPACE}/{slug}-{digest}"
        path = self._root / f"{slug}-{digest}"
        self._validate_branch_name(branch_name)
        return TaskWorktreeRecord(
            task_id=normalized,
            base_commit=task_base,
            branch_name=branch_name,
            path=path,
        )

    def create(
        self,
        task_id: str,
        *,
        base_commit: str | None = None,
    ) -> TaskWorktreeRecord:
        """Create a locked linked worktree and a fresh task branch from an immutable commit."""

        if self._base_workspace.changed_files():
            raise TaskWorktreeError(
                "base workspace became dirty after the worktree base was frozen"
            )

        record = self.record_for(task_id, base_commit=base_commit)
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
            raise TaskWorktreeCollisionError(f"task branch already exists: {record.branch_name}")

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

    def commit_task_changes(self, task_id: str) -> str:
        """Commit one successful task without hooks or host Git-identity dependencies."""

        record = self.record_for(task_id)
        entry = self._find_registered_path(record.path, self._registered_worktrees())
        if entry is None:
            raise TaskWorktreeError(f"task worktree is not registered: {record.task_id}")
        self._assert_owned_registration(record, entry)
        if not record.path.exists() or entry.prunable_reason is not None:
            raise StaleTaskWorktreeError(
                f"cannot finalize stale task worktree registration: {record.task_id}"
            )

        workspace = LocalGitWorkspace(
            record.path,
            git_timeout_seconds=self._git_timeout_seconds,
        )
        changed = workspace.changed_files()
        if not changed:
            raise TaskWorktreeError("successful task worktree has no changes to commit")

        current_branch = self._git_at(
            record.path,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
        ).stdout.strip()
        if current_branch != record.branch_name:
            raise TaskWorktreeCollisionError(
                "task worktree changed branches before successful finalization"
            )

        parent = self._git_at(
            record.path,
            ["rev-parse", "--verify", "HEAD^{commit}"],
        ).stdout.strip()
        self._git_at(record.path, ["add", "--all"])
        tree = self._git_at(record.path, ["write-tree"]).stdout.strip()
        commit = self._git_at(
            record.path,
            [
                "commit-tree",
                tree,
                "-p",
                parent,
                "-m",
                f"DevFlow task {record.task_id}",
            ],
            env=self._commit_environment(),
        ).stdout.strip()
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            raise TaskWorktreeError("Git did not return a full task commit id")

        self._git_at(
            record.path,
            [
                "update-ref",
                f"refs/heads/{record.branch_name}",
                commit,
                parent,
            ],
        )
        if workspace.changed_files():
            raise TaskWorktreeError("task worktree must be clean after successful finalization")

        branch_head = self._git_at(
            record.path,
            ["rev-parse", "--verify", f"refs/heads/{record.branch_name}^{{commit}}"],
        ).stdout.strip()
        if branch_head != commit:
            raise TaskWorktreeError("task branch does not point at the finalized task commit")
        return commit

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
            raise TaskWorktreeError(
                "Git reported removal success but the worktree path still exists"
            )
        if self._find_registered_path(record.path, self._registered_worktrees()) is not None:
            raise TaskWorktreeError(
                "Git reported removal success but worktree metadata still exists"
            )
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
            raise TaskWorktreeError("new task worktree does not point at its requested base commit")
        branch = self._git_at(
            record.path,
            ["symbolic-ref", "--quiet", "--short", "HEAD"],
        ).stdout.strip()
        if branch != record.branch_name:
            raise TaskWorktreeError(
                "new task worktree is not checked out on its reserved task branch"
            )

    def _freeze_base_commit(self, *, current_head: str, requested: str | None) -> str:
        if requested is None:
            return current_head
        if _COMMIT_PATTERN.fullmatch(requested) is None:
            raise ValueError("frozen base commit must be a full 40-64 character hexadecimal id")

        resolved = self._git(
            ["rev-parse", "--verify", f"{requested}^{{commit}}"],
            check=False,
        )
        canonical = resolved.stdout.strip()
        if resolved.returncode != 0 or _COMMIT_PATTERN.fullmatch(canonical) is None:
            raise TaskWorktreeError("frozen base commit does not exist in the repository")

        ancestor = self._git(
            ["merge-base", "--is-ancestor", canonical, current_head],
            check=False,
        )
        if ancestor.returncode == 0:
            return canonical
        if ancestor.returncode != 1:
            raise TaskWorktreeError("Git could not validate frozen base ancestry")

        descendant = self._git(
            ["merge-base", "--is-ancestor", current_head, canonical],
            check=False,
        )
        if descendant.returncode == 0:
            return canonical
        if descendant.returncode != 1:
            raise TaskWorktreeError("Git could not validate frozen base ancestry")

        raise TaskWorktreeError(
            "frozen base commit must remain on the managed repository HEAD ancestry"
        )

    def _resolve_task_base(self, requested: str | None) -> str:
        if requested is None:
            return self._base_commit
        if _COMMIT_PATTERN.fullmatch(requested) is None:
            raise ValueError("task base commit must be a full 40-64 character hexadecimal id")

        resolved = self._git(
            ["rev-parse", "--verify", f"{requested}^{{commit}}"],
            check=False,
        )
        canonical = resolved.stdout.strip()
        if resolved.returncode != 0 or _COMMIT_PATTERN.fullmatch(canonical) is None:
            raise TaskWorktreeError("requested task base commit does not exist in the repository")

        ancestry = self._git(
            ["merge-base", "--is-ancestor", self._base_commit, canonical],
            check=False,
        )
        if ancestry.returncode == 1:
            raise TaskWorktreeError(
                "requested task base commit must descend from the frozen run base"
            )
        if ancestry.returncode != 0:
            raise TaskWorktreeError("Git could not validate the requested task base ancestry")
        return canonical

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
        if (
            not task_id
            or task_id != task_id.strip()
            or len(task_id) > 128
            or _TASK_ID_PATTERN.fullmatch(task_id) is None
        ):
            raise ValueError("task_id must use the TaskContract task_id format")
        return task_id

    @staticmethod
    def _commit_environment() -> dict[str, str]:
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "DevFlow",
            "GIT_AUTHOR_EMAIL": "devflow@local.invalid",
            "GIT_COMMITTER_NAME": "DevFlow",
            "GIT_COMMITTER_EMAIL": "devflow@local.invalid",
        }

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
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise TaskWorktreeError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise TaskWorktreeError("git worktree command exceeded the configured timeout") from exc

        if check and completed.returncode != 0:
            stderr = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise TaskWorktreeError(f"git worktree command failed: {stderr}")
        return completed
