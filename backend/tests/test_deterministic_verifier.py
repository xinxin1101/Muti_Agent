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


def _make_repository(
    tmp_path: Path,
    *,
    test_body: str,
) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    tests = root / "tests"
    tests.mkdir()

    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_module.py").write_text(test_body, encoding="utf-8")

    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task(*commands: str) -> TaskContract:
    return TaskContract(
        task_id="VERIFY-001",
        objective="Update the module value and verify the repository.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["The repository passes deterministic checks."],
        verification_commands=list(commands),
    )


def test_passing_project_returns_hard_gate_pass(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=(
            "from module import VALUE\n\n\n"
            "def test_value():\n"
            "    assert VALUE == 2\n"
        ),
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = DeterministicVerifier().verify(
        _task("pytest -q", "ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is True
    assert [check.name for check in result.checks] == ["git_scope", "pytest", "ruff"]
    assert all(check.passed for check in result.checks)
    assert result.checks[1].exit_code == 0
    assert result.checks[2].exit_code == 0


def test_failing_test_is_classified_as_test_failure(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=(
            "from module import VALUE\n\n\n"
            "def test_value():\n"
            "    assert VALUE == 3\n"
        ),
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    verifier = DeterministicVerifier()

    result = verifier.verify(_task("pytest -q"), workspace=LocalGitWorkspace(root))
    reports = verifier.failure_reports(result)

    assert result.passed is False
    pytest_check = result.checks[1]
    assert pytest_check.check_type.value == "test"
    assert pytest_check.failure_type is FailureType.TEST_FAILURE
    assert pytest_check.exit_code != 0
    assert reports[0].failure_type is FailureType.TEST_FAILURE
    assert reports[0].retryable is True
    assert any("VALUE == 3" in item or "assert 2 == 3" in item for item in reports[0].evidence)


def test_lint_failure_is_distinguishable_from_test_failure(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=(
            "from module import VALUE\n\n\n"
            "def test_value():\n"
            "    assert VALUE == 2\n"
        ),
    )
    (root / "module.py").write_text("import os\n\nVALUE = 2\n", encoding="utf-8")

    result = DeterministicVerifier().verify(
        _task("pytest -q", "ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].passed is True
    assert result.checks[2].passed is False
    assert result.checks[2].failure_type is FailureType.LINT_FAILURE
    assert "F401" in result.checks[2].stdout


def test_scope_violation_prevents_process_execution(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body="def test_ok():\n    assert True\n",
    )
    (root / "tests" / "test_module.py").write_text(
        "def test_tampered():\n    assert False\n",
        encoding="utf-8",
    )

    result = DeterministicVerifier().verify(
        _task("pytest -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert len(result.checks) == 1
    assert result.checks[0].name == "git_scope"
    assert result.checks[0].failure_type is FailureType.SCOPE_VIOLATION
    assert "READ_ONLY:tests/test_module.py" in result.checks[0].stderr


def test_unsupported_command_is_rejected_without_shell_execution(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body="def test_ok():\n    assert True\n",
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    marker = root / "should-not-exist.txt"

    result = DeterministicVerifier().verify(
        _task("python -c \"open('should-not-exist.txt', 'w').write('bad')\""),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert marker.exists() is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert "Only pytest" in result.checks[1].stderr


def test_verification_command_timeout_is_bounded(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=(
            "import time\n\n\n"
            "def test_slow():\n"
            "    time.sleep(1.0)\n"
            "    assert True\n"
        ),
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = DeterministicVerifier(command_timeout_seconds=0.1).verify(
        _task("pytest -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert result.checks[1].exit_code is None
    assert "timed out" in result.checks[1].stderr.lower()


def test_python_module_forms_are_supported(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=(
            "from module import VALUE\n\n\n"
            "def test_value():\n"
            "    assert VALUE == 2\n"
        ),
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = DeterministicVerifier().verify(
        _task("python -m pytest -q", "python -m ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is True
    assert [check.name for check in result.checks[1:]] == ["pytest", "ruff"]
