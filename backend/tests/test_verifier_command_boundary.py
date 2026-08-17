import subprocess
from pathlib import Path

from app.models import FailureType, TaskContract
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    return root


def _task(command: str) -> TaskContract:
    return TaskContract(
        task_id="VERIFY-BOUNDARY-001",
        objective="Verify without escaping the managed workspace.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=[],
        acceptance_criteria=["Verification stays inside the repository."],
        verification_commands=[command],
    )


def test_parent_traversal_argument_is_rejected_before_pytest_runs(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    marker = tmp_path / "outside-ran.txt"
    outside_test = tmp_path / "outside_test.py"
    outside_test.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "def test_outside():\n"
        "    assert True\n",
        encoding="utf-8",
    )

    result = DeterministicVerifier().verify(
        _task("pytest ../outside_test.py"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert marker.exists() is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "traverse workspace" in result.checks[1].stderr


def test_absolute_option_value_is_rejected_before_pytest_runs(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    result = DeterministicVerifier().verify(
        _task("pytest --rootdir=/tmp -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "inside workspace" in result.checks[1].stderr
