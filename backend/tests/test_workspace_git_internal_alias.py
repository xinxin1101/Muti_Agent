import json
import subprocess
from pathlib import Path

from app.models import TaskContract, ToolCall
from app.tools import RepositoryToolbox
from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_dot_git_alias_is_denied_even_with_broad_writable_scope(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    task = TaskContract(
        task_id="GIT-ALIAS-001",
        objective="Exercise a broad write contract safely.",
        readable_files=["**"],
        writable_files=["**"],
        readonly_files=[],
        acceptance_criteria=["Git metadata remains protected."],
        verification_commands=["pytest -q"],
    )
    toolbox = RepositoryToolbox(workspace=LocalGitWorkspace(root), task=task)
    call = ToolCall(
        id="write-1",
        name="write_file",
        arguments=json.dumps({"path": "./.git/config", "content": "tampered"}),
    )

    result = toolbox.execute(call)

    assert result.ok is False
    assert "git" in result.content.lower()
    assert "tampered" not in (root / ".git" / "config").read_text(encoding="utf-8")
