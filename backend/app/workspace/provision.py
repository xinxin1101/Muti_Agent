from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from app.workspace.git import LocalGitWorkspace, WorkspaceGitError


class ProjectProvisionError(RuntimeError):
    """Raised when a repository cannot be materialized into the managed workspace."""


class ManagedProjectProvisioner:
    """Clone or validate one managed project repository without browser-held credentials."""

    def __init__(self, root: str | Path, *, git_timeout_seconds: float = 120.0) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        candidate = Path(root).expanduser()
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("managed repository root must not be a symbolic link")
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()
        self._git_timeout_seconds = git_timeout_seconds

    @property
    def root(self) -> Path:
        return self._root

    def project_path(self, project_id: UUID) -> Path:
        return self._root / str(project_id)

    def is_ready(self, project_id: UUID) -> bool:
        path = self.project_path(project_id)
        if not path.is_dir():
            return False
        try:
            LocalGitWorkspace(path)
        except (ValueError, WorkspaceGitError):
            return False
        return True

    def provision(self, project_id: UUID, *, repository_url: str, default_branch: str) -> None:
        repository = self._validate_repository_url(repository_url)
        branch = default_branch.strip()
        if not branch:
            raise ValueError("default_branch must not be empty")

        target = self.project_path(project_id)
        if target.exists():
            workspace = LocalGitWorkspace(target)
            origin = self._git(
                ["-C", str(workspace.root), "config", "--get", "remote.origin.url"]
            ).strip()
            if origin != repository:
                raise ProjectProvisionError(
                    "managed project workspace origin does not match persisted repository URL"
                )
            return

        try:
            self._git(
                [
                    "clone",
                    "--branch",
                    branch,
                    "--single-branch",
                    "--",
                    repository,
                    str(target),
                ]
            )
            LocalGitWorkspace(target)
        except Exception:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise

    @staticmethod
    def _validate_repository_url(value: str) -> str:
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("repository_url must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("repository_url must not embed credentials")
        return normalized

    def _git(self, arguments: list[str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *arguments],
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProjectProvisionError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProjectProvisionError("git project provisioning timed out") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "unknown git error"
            raise ProjectProvisionError(f"git project provisioning failed: {detail}")
        return completed.stdout
