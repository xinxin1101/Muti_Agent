from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.workspace import ManagedProjectProvisioner


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
