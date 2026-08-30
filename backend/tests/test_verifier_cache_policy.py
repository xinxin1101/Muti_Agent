from __future__ import annotations

import subprocess
from pathlib import Path

from app.models import TaskContract
from app.verification import DeterministicVerifier, LocalProcessVerificationRunner
from app.workspace import LocalGitWorkspace


def test_ruff_verification_disables_workspace_cache(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "init"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "devflow-tests@example.com"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "DevFlow Tests"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
        text=True,
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    task = TaskContract(
        task_id="RUFF-NO-CACHE",
        objective="Lint the changed module without verifier side effects.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=[],
        acceptance_criteria=["Ruff passes without creating workspace cache files."],
        verification_commands=["ruff check ."],
        max_retries=1,
    )

    result = DeterministicVerifier(command_runner=LocalProcessVerificationRunner()).verify(
        task, workspace=LocalGitWorkspace(root)
    )

    assert result.passed is True
    assert (root / ".ruff_cache").exists() is False
