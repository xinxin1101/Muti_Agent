from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from app.models.project import canonical_repository_url
from app.workspace.git import LocalGitWorkspace, WorkspaceGitError

_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")


class ProjectProvisionError(RuntimeError):
    """Raised when a repository cannot be materialized into the managed workspace."""

    def __init__(self, message: str, *, code: str = "PROJECT_PROVISION_FAILED") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceReadiness:
    ready: bool
    detail: str
    head_commit: str | None = None


class ManagedProjectProvisioner:
    """Provision and fetch managed repositories without changing Run-bound Git truth."""

    def __init__(
        self,
        root: str | Path,
        *,
        git_timeout_seconds: float = 300.0,
        read_token: SecretStr | None = None,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        candidate = Path(root).expanduser()
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("managed repository root must not be a symbolic link")
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()
        self._git_timeout_seconds = git_timeout_seconds
        self._read_token = read_token

    @property
    def root(self) -> Path:
        return self._root

    def project_path(self, project_id: UUID) -> Path:
        return self._root / str(project_id)

    def is_ready(self, project_id: UUID) -> bool:
        """Legacy local-only readiness retained for focused V1 callers/tests."""

        path = self.project_path(project_id)
        if path.is_symlink() or not path.is_dir():
            return False
        try:
            LocalGitWorkspace(path)
        except (ValueError, WorkspaceGitError):
            return False
        return not (path / ".gitmodules").exists()

    def readiness(
        self,
        project_id: UUID,
        *,
        repository_url: str,
        default_branch: str,
    ) -> WorkspaceReadiness:
        repository = canonical_repository_url(repository_url)
        branch = self._branch(default_branch)
        target = self.project_path(project_id)
        if target.is_symlink() or not target.is_dir():
            return WorkspaceReadiness(False, "managed project directory is missing or unsafe")
        if (target / ".gitmodules").exists():
            return WorkspaceReadiness(False, "Git submodule projects are not supported yet")
        try:
            workspace = LocalGitWorkspace(target)
            origin = self._git(
                ["-C", str(workspace.root), "config", "--get", "remote.origin.url"]
            ).strip()
            if canonical_repository_url(origin) != repository:
                return WorkspaceReadiness(False, "managed repository origin identity mismatch")
            current_branch = self._git(
                ["-C", str(workspace.root), "symbolic-ref", "--short", "HEAD"]
            ).strip()
            if current_branch != branch:
                return WorkspaceReadiness(False, "managed repository default branch mismatch")
            dirty = self._git(
                ["-C", str(workspace.root), "status", "--porcelain", "--untracked-files=all"]
            ).strip()
            if dirty:
                return WorkspaceReadiness(False, "managed base workspace contains local changes")
            head = workspace.head_commit()
        except (ValueError, WorkspaceGitError, ProjectProvisionError):
            return WorkspaceReadiness(False, "managed repository failed Git identity validation")
        return WorkspaceReadiness(True, "managed repository identity is ready", head_commit=head)

    def provision(self, project_id: UUID, *, repository_url: str, default_branch: str) -> None:
        repository = canonical_repository_url(repository_url)
        branch = self._branch(default_branch)
        target = self.project_path(project_id)
        if target.is_symlink():
            raise ProjectProvisionError(
                "managed project workspace must not be a symbolic link",
                code="WORKSPACE_SYMLINK",
            )
        if target.exists():
            state = self.readiness(
                project_id,
                repository_url=repository,
                default_branch=branch,
            )
            if not state.ready:
                raise ProjectProvisionError(state.detail, code="WORKSPACE_IDENTITY_MISMATCH")
            return

        try:
            if self._is_empty_remote(repository):
                self._initialize_empty_workspace(
                    target,
                    repository_url=repository,
                    default_branch=branch,
                )
                return
            self._git(
                [
                    "clone",
                    "--filter=blob:none",
                    "--branch",
                    branch,
                    "--single-branch",
                    "--",
                    repository,
                    str(target),
                ],
                authenticated=self._is_github(repository),
            )
            LocalGitWorkspace(target)
            if (target / ".gitmodules").exists():
                raise ProjectProvisionError(
                    "Git submodule projects are not supported by this release",
                    code="SUBMODULE_PROJECT_UNSUPPORTED",
                )
        except Exception:
            if target.exists() and not target.is_symlink():
                shutil.rmtree(target, ignore_errors=True)
            raise

    def synchronize(
        self,
        project_id: UUID,
        *,
        repository_url: str,
        default_branch: str,
    ) -> str:
        """Fetch the remote branch and return its immutable commit without pull/reset."""

        repository = canonical_repository_url(repository_url)
        branch = self._branch(default_branch)
        state = self.readiness(
            project_id,
            repository_url=repository,
            default_branch=branch,
        )
        if not state.ready:
            raise ProjectProvisionError(state.detail, code="WORKSPACE_NOT_READY")
        target = self.project_path(project_id)
        remote_branch = self._git(
            ["ls-remote", "--heads", repository, f"refs/heads/{branch}"],
            authenticated=self._is_github(repository),
        ).strip()
        if not remote_branch:
            # An empty remote has no default branch yet.  Its local bootstrap
            # commit is still valid immutable truth for the first DevFlow run.
            return LocalGitWorkspace(target).head_commit()
        self._git(
            [
                "-C",
                str(target),
                "fetch",
                "--no-tags",
                "--prune",
                "origin",
                branch,
            ],
            authenticated=self._is_github(repository),
        )
        commit = (
            self._git(["-C", str(target), "rev-parse", f"refs/remotes/origin/{branch}^{{commit}}"])
            .strip()
            .lower()
        )
        if _COMMIT_RE.fullmatch(commit) is None:
            raise ProjectProvisionError(
                "remote branch did not resolve to a valid commit",
                code="REMOTE_COMMIT_INVALID",
            )
        return commit

    def _is_empty_remote(self, repository_url: str) -> bool:
        """Return true only when Git can reach a repository with no HEAD/ref."""

        head = self._git(
            ["ls-remote", "--symref", repository_url, "HEAD"],
            authenticated=self._is_github(repository_url),
        )
        return not head.strip()

    def _initialize_empty_workspace(
        self,
        target: Path,
        *,
        repository_url: str,
        default_branch: str,
    ) -> None:
        """Create local-only bootstrap truth for a reachable empty remote."""

        self._git(["init", f"--initial-branch={default_branch}", str(target)])
        self._git(["-C", str(target), "remote", "add", "origin", repository_url])
        self._git(["-C", str(target), "config", "user.name", "DevFlow"])
        self._git(["-C", str(target), "config", "user.email", "devflow@local.invalid"])
        self._git(
            [
                "-C",
                str(target),
                "commit",
                "--allow-empty",
                "-m",
                "chore: initialize DevFlow workspace",
            ]
        )
        LocalGitWorkspace(target)

    @staticmethod
    def _branch(value: str) -> str:
        branch = value.strip()
        if not branch:
            raise ValueError("default_branch must not be empty")
        if branch.startswith("-") or ".." in branch or any(ch.isspace() for ch in branch):
            raise ValueError("default_branch is not a safe Git branch name")
        return branch

    @staticmethod
    def _is_github(repository_url: str) -> bool:
        return repository_url.lower().startswith("https://github.com/")

    def _git(self, arguments: list[str], *, authenticated: bool = False) -> str:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        if authenticated and self._read_token is not None:
            token = self._read_token.get_secret_value().strip()
            if token:
                credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
                env["GIT_CONFIG_COUNT"] = "1"
                env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
                env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"
        try:
            completed = subprocess.run(
                ["git", *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ProjectProvisionError(
                "git executable is not available",
                code="GIT_UNAVAILABLE",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProjectProvisionError(
                "git project provisioning/sync timed out",
                code="GIT_TIMEOUT",
            ) from exc
        if completed.returncode != 0:
            raise ProjectProvisionError(
                "git project provisioning/sync failed without exposing remote credentials",
                code="GIT_COMMAND_FAILED",
            )
        return completed.stdout
