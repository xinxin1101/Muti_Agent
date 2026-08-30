from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.workspace import ManagedProjectProvisioner, ProjectProvisionError


def test_project_provisioner_rejects_non_https_repository_urls(tmp_path: Path) -> None:
    provisioner = ManagedProjectProvisioner(tmp_path)

    with pytest.raises(ValueError, match="HTTPS"):
        provisioner.provision(
            uuid4(),
            repository_url="file:///tmp/repo",
            default_branch="main",
        )


def test_project_provisioner_rejects_embedded_credentials(tmp_path: Path) -> None:
    provisioner = ManagedProjectProvisioner(tmp_path)

    with pytest.raises(ValueError, match="credentials"):
        provisioner.provision(
            uuid4(),
            repository_url="https://token@example.com/repo.git",
            default_branch="main",
        )


def test_project_provisioner_rejects_project_symlink(tmp_path: Path) -> None:
    provisioner = ManagedProjectProvisioner(tmp_path / "repos")
    project_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        provisioner.project_path(project_id).symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symbolic-link privilege is unavailable")
        raise

    assert provisioner.is_ready(project_id) is False
    with pytest.raises(ProjectProvisionError, match="symbolic link"):
        provisioner.provision(
            project_id,
            repository_url="https://example.com/repo.git",
            default_branch="main",
        )


def test_empty_remote_creates_a_local_bootstrap_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provisioner = ManagedProjectProvisioner(tmp_path / "repos")
    project_id = uuid4()
    repository_url = "https://github.com/example/empty-repository.git"
    monkeypatch.setattr(provisioner, "_is_empty_remote", lambda _url: True)

    provisioner.provision(
        project_id,
        repository_url=repository_url,
        default_branch="main",
    )

    workspace = provisioner.project_path(project_id)
    assert provisioner.readiness(
        project_id,
        repository_url=repository_url,
        default_branch="main",
    ).ready
    assert (workspace / ".git").is_dir()
    head = (workspace / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    assert head == "ref: refs/heads/main"
