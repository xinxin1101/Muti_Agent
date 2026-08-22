from __future__ import annotations

import subprocess
from pathlib import Path

from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.persistence.errors import PersistenceCorruptionError
from app.workspace import LocalGitWorkspace, TaskWorktreeRecord


class GenerationBoundWorktreeView:
    """Read-only bridge from Phase 5 worker evidence to the Phase 2 merge queue contract.

    Production workers create generation-bound worktrees, while TopologicalMergeQueue consumes a
    task-keyed manager view. This adapter does not create or mutate worktrees. It resolves the
    exact registered Git worktree named by accepted WorkerExecutionEvidence and exposes only the
    `base_commit`/`record_for` surface the merge queue needs for its own Git revalidation.
    """

    def __init__(
        self,
        *,
        workspace: LocalGitWorkspace,
        run_base_commit: str,
        executions: dict[str, WorkerExecutionEvidence],
        git_timeout_seconds: float = 10.0,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        self._workspace = workspace
        self._base_commit = run_base_commit
        self._executions = dict(executions)
        self._git_timeout_seconds = git_timeout_seconds

    @property
    def base_commit(self) -> str:
        return self._base_commit

    def record_for(
        self,
        task_id: str,
        *,
        base_commit: str | None = None,
    ) -> TaskWorktreeRecord:
        execution = self._executions.get(task_id)
        if execution is None:
            raise PersistenceCorruptionError(
                f"integration requested unavailable worker evidence for task {task_id!r}"
            )
        if execution.status is not WorkerExecutionStatus.SUCCEEDED:
            raise PersistenceCorruptionError(
                f"integration requested non-successful worker evidence for task {task_id!r}"
            )
        if execution.branch_name is None or execution.commit_sha is None:
            raise PersistenceCorruptionError(
                f"successful worker evidence for {task_id!r} lacks Git identity"
            )
        if base_commit is not None and base_commit != execution.base_commit:
            raise PersistenceCorruptionError(
                f"integration base for {task_id!r} disagrees with worker evidence"
            )

        matches = [
            item
            for item in self._registered_worktrees()
            if item.branch_ref == f"refs/heads/{execution.branch_name}"
        ]
        if len(matches) != 1:
            raise PersistenceCorruptionError(
                f"expected exactly one registered generation worktree for {task_id!r}; "
                f"found {len(matches)}"
            )
        registered = matches[0]
        if registered.head != execution.commit_sha:
            raise PersistenceCorruptionError(
                f"registered worktree HEAD for {task_id!r} moved after terminal evidence"
            )
        if not registered.path.exists():
            raise PersistenceCorruptionError(
                f"registered generation worktree for {task_id!r} is missing on disk"
            )
        return TaskWorktreeRecord(
            task_id=task_id,
            base_commit=execution.base_commit,
            branch_name=execution.branch_name,
            path=registered.path,
        )

    def _registered_worktrees(self) -> tuple[_RegisteredGenerationWorktree, ...]:
        result = self._git(["worktree", "list", "--porcelain"])
        records: list[_RegisteredGenerationWorktree] = []
        current: dict[str, str] = {}
        for line in [*result.stdout.splitlines(), ""]:
            if not line:
                if current:
                    path = current.get("worktree")
                    if path is None:
                        raise PersistenceCorruptionError(
                            "Git worktree registry returned an entry without a path"
                        )
                    records.append(
                        _RegisteredGenerationWorktree(
                            path=Path(path).resolve(strict=False),
                            head=current.get("HEAD"),
                            branch_ref=current.get("branch"),
                        )
                    )
                    current = {}
                continue
            key, separator, value = line.partition(" ")
            if separator and key in {"worktree", "HEAD", "branch"}:
                current[key] = value
        return tuple(records)

    def _git(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self._workspace.root), *arguments],
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PersistenceCorruptionError("git executable is unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise PersistenceCorruptionError(
                "Git worktree registry inspection exceeded its bounded timeout"
            ) from exc
        if result.returncode != 0:
            raise PersistenceCorruptionError(
                "Git worktree registry inspection failed: "
                f"exit_code={result.returncode}"
            )
        return result


class _RegisteredGenerationWorktree:
    __slots__ = ("path", "head", "branch_ref")

    def __init__(self, *, path: Path, head: str | None, branch_ref: str | None) -> None:
        self.path = path
        self.head = head
        self.branch_ref = branch_ref
