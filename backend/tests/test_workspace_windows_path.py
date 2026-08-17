import subprocess
from pathlib import Path

import pytest

from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_workspace_rejects_windows_drive_prefix_on_posix_runner(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    workspace = LocalGitWorkspace(root)

    with pytest.raises(ValueError, match="Windows drive prefix"):
        workspace.resolve_path("C:/outside.txt")
