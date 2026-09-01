from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


class LocalProjectCleanupError(RuntimeError):
    """Raised when a local artifact is unsafe to remove or cannot be removed."""


@dataclass(frozen=True)
class LocalProjectArtifacts:
    """Project-owned local paths only; shared dependency caches are intentionally excluded."""

    repository_root: Path
    project_cache_root: Path

    def workspace_path(self, project_id: UUID) -> Path:
        return self.repository_root / str(project_id)

    def cache_path(self, project_id: UUID) -> Path:
        return self.project_cache_root / str(project_id)

    def sizes(self, project_id: UUID) -> tuple[int, int]:
        return self._tree_size(self.workspace_path(project_id)), self._tree_size(
            self.cache_path(project_id)
        )

    def remove(self, project_id: UUID) -> tuple[int, int]:
        workspace, cache = self.sizes(project_id)
        self._remove_owned_path(self.workspace_path(project_id), self.repository_root)
        self._remove_owned_path(self.cache_path(project_id), self.project_cache_root)
        return workspace, cache

    @staticmethod
    def _tree_size(path: Path) -> int:
        if not path.exists() or path.is_symlink():
            return 0
        return sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file() and not item.is_symlink()
        )

    @staticmethod
    def _remove_owned_path(path: Path, root: Path) -> None:
        root = root.resolve()
        if path.parent.resolve() != root:
            raise LocalProjectCleanupError(
                "refusing to delete a path outside the managed project root"
            )
        if not path.exists():
            return
        if path.is_symlink():
            raise LocalProjectCleanupError("refusing to delete a symbolic-link project artifact")
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise LocalProjectCleanupError(
                f"could not remove local project artifact: {path}"
            ) from exc
