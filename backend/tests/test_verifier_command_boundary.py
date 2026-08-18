import subprocess
from pathlib import Path

from app.models import FailureType, TaskContract
from app.verification import DeterministicVerifier, LocalProcessVerificationRunner
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
    tests = root / "tests"
    tests.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_module.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_value_is_two() -> None:\n"
        "    assert 'VALUE = 2' in Path('module.py').read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
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
        readonly_files=["tests/**"],
        acceptance_criteria=["Verification stays inside the repository."],
        verification_commands=[command],
    )


def _verifier() -> DeterministicVerifier:
    return DeterministicVerifier(command_runner=LocalProcessVerificationRunner())


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

    result = _verifier().verify(
        _task("pytest ../outside_test.py"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert marker.exists() is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "traverse workspace" in result.checks[1].stderr


def test_absolute_option_value_is_rejected_before_pytest_runs(tmp_path: Path) -> None:
    root = _repository(tmp_path)

    result = _verifier().verify(
        _task("pytest --rootdir=/tmp -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "inside workspace" in result.checks[1].stderr


def test_pytest_verification_does_not_leave_cache_or_bytecode_changes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)

    result = _verifier().verify(
        _task("pytest -q"),
        workspace=workspace,
    )

    assert result.passed is True
    assert workspace.changed_files() == ["module.py"]
    assert (root / ".pytest_cache").exists() is False
    assert (root / "tests" / "__pycache__").exists() is False


def test_ruff_fix_mode_is_rejected_before_it_can_mutate_source(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    original = "import os\n\nVALUE = 2\n"
    (root / "module.py").write_text(original, encoding="utf-8")

    result = _verifier().verify(
        _task("ruff check --fix ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "must not mutate" in result.checks[1].stderr
    assert (root / "module.py").read_text(encoding="utf-8") == original


def test_post_verification_scope_detects_test_generated_out_of_scope_file(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    test_file = root / "tests" / "test_module.py"
    test_file.write_text(
        "from pathlib import Path\n\n\n"
        "def test_generates_file() -> None:\n"
        "    Path('generated.txt').write_text('artifact', encoding='utf-8')\n"
        "    assert True\n",
        encoding="utf-8",
    )
    _git(root, "add", "tests/test_module.py", "module.py")
    _git(root, "commit", "-m", "prepare side-effect fixture")
    (root / "module.py").write_text("VALUE = 3\n", encoding="utf-8")

    result = _verifier().verify(
        _task("pytest -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    post_scope = result.checks[-1]
    assert post_scope.name == "git_scope_post_verification"
    assert post_scope.failure_type is FailureType.SCOPE_VIOLATION
    assert "OUT_OF_SCOPE:generated.txt" in post_scope.stderr
