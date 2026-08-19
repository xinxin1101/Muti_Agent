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
    provisioner.project_path(project_id).symlink_to(outside, target_is_directory=True)

    assert provisioner.is_ready(project_id) is False
    with pytest.raises(ProjectProvisionError, match="symbolic link"):
        provisioner.provision(
            project_id,
            repository_url="https://example.com/repo.git",
            default_branch="main",
        )
