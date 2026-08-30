import subprocess
from pathlib import Path

from app.models import FailureType, TaskContract
from app.models.verification import VerificationBackend
from app.verification import DeterministicVerifier, LocalProcessVerificationRunner
from app.verification.sandbox import VerificationExecution
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


def _file_content_test(expected: str) -> str:
    return (
        "from pathlib import Path\n\n\n"
        "def test_value():\n"
        f"    assert {expected!r} in Path('module.py').read_text(encoding='utf-8')\n"
    )


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


def _local_verifier(**kwargs) -> DeterministicVerifier:
    return DeterministicVerifier(
        command_runner=LocalProcessVerificationRunner(),
        **kwargs,
    )


class _SandboxedFailingRunner:
    @property
    def is_sandboxed(self) -> bool:
        return True

    def run(self, argv, *, workspace, timeout_seconds):
        del argv, workspace, timeout_seconds
        return VerificationExecution(
            exit_code=1,
            stdout="",
            stderr="AssertionError: Board API does not meet the contract",
            duration_ms=1,
            backend=VerificationBackend.DOCKER,
            details=("rootfs=readonly",),
        )


class _PythonOutputRunner:
    @property
    def is_sandboxed(self) -> bool:
        return True

    def run(self, argv, *, workspace, timeout_seconds):
        del workspace, timeout_seconds
        assert argv == ["python3", "hello.py"]
        return VerificationExecution(
            exit_code=0,
            stdout="hello world\n",
            stderr="",
            duration_ms=1,
            backend=VerificationBackend.DOCKER,
            details=("rootfs=readonly",),
        )


class _StageRecordingRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    @property
    def is_sandboxed(self) -> bool:
        return True

    def run(self, argv, *, workspace, timeout_seconds):
        del workspace, timeout_seconds
        command = list(argv)
        self.commands.append(command)
        return VerificationExecution(
            exit_code=1 if "tests/test_module.py" in command else 0,
            stdout="",
            stderr="targeted failure",
            duration_ms=1,
            backend=VerificationBackend.DOCKER,
        )


def test_passing_project_returns_hard_gate_pass(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body=_file_content_test("VALUE = 2"))
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = _local_verifier().verify(
        _task("pytest -q", "ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is True
    assert [check.name for check in result.checks] == ["git_scope", "pytest", "ruff"]
    assert all(check.passed for check in result.checks)
    assert result.checks[1].exit_code == 0
    assert result.checks[2].exit_code == 0
    assert result.checks[1].execution_backend.value == "host"


def test_failing_test_is_classified_as_test_failure(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body=_file_content_test("VALUE = 3"))
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    verifier = _local_verifier()

    result = verifier.verify(_task("pytest -q"), workspace=LocalGitWorkspace(root))
    reports = verifier.failure_reports(result)

    assert result.passed is False
    pytest_check = result.checks[1]
    assert pytest_check.check_type.value == "test"
    assert pytest_check.failure_type is FailureType.TEST_FAILURE
    assert pytest_check.exit_code != 0
    assert reports[0].failure_type is FailureType.TEST_FAILURE
    assert reports[0].retryable is True
    assert any("VALUE = 3" in item for item in reports[0].evidence)


def test_targeted_verification_runs_before_and_short_circuits_broad_checks(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body="def test_ok():\n    assert True\n")
    runner = _StageRecordingRunner()
    result = DeterministicVerifier(command_runner=runner).verify(
        _task("pytest -q", "pytest tests/test_module.py -q", "ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert runner.commands == [
        ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests/test_module.py", "-q"]
    ]
    assert result.checks[-1].execution_details[-1] == "verification_stage=fast"


def test_failing_custom_sandbox_check_is_repairable_test_failure(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body="def test_ok():\n    assert True\n")
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    verifier = DeterministicVerifier(command_runner=_SandboxedFailingRunner())

    result = verifier.verify(
        _task("python -c \"assert False, 'contract mismatch'\""),
        workspace=LocalGitWorkspace(root),
    )
    reports = verifier.failure_reports(result)

    assert result.checks[1].failure_type is FailureType.TEST_FAILURE
    assert reports[0].retryable is True
    assert reports[0].message == "Deterministic custom verification failed."


def test_python_stdout_shell_style_assertion_is_compiled_without_a_shell(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body="def test_ok():\n    assert True\n")
    verifier = DeterministicVerifier(command_runner=_PythonOutputRunner())

    result = verifier.verify(
        _task('test "$(python3 hello.py)" = "hello world"'),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is True
    assert result.checks[1].name == "python_stdout"


def test_lint_failure_is_distinguishable_from_test_failure(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body=_file_content_test("VALUE = 2"))
    (root / "module.py").write_text("import os\n\nVALUE = 2\n", encoding="utf-8")

    result = _local_verifier().verify(
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

    result = _local_verifier().verify(
        _task("pytest -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert len(result.checks) == 1
    assert result.checks[0].name == "git_scope"
    assert result.checks[0].failure_type is FailureType.SCOPE_VIOLATION
    assert "READ_ONLY:tests/test_module.py" in result.checks[0].stderr


def test_custom_command_is_rejected_on_explicit_host_runner(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body="def test_ok():\n    assert True\n",
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    marker = root / "should-not-exist.txt"

    result = _local_verifier().verify(
        _task("python -c \"open('should-not-exist.txt', 'w').write('bad')\""),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert marker.exists() is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert result.checks[1].name == "sandbox_required"
    assert "require the Docker sandbox" in result.checks[1].stderr


def test_verification_command_timeout_is_bounded(tmp_path: Path) -> None:
    root = _make_repository(
        tmp_path,
        test_body=("import time\n\n\ndef test_slow():\n    time.sleep(1.0)\n    assert True\n"),
    )
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = _local_verifier(command_timeout_seconds=0.1).verify(
        _task("pytest -q"),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is False
    assert result.checks[1].failure_type is FailureType.TOOL_FAILURE
    assert result.checks[1].exit_code is None
    assert "timed out" in result.checks[1].stderr.lower()


def test_python_module_forms_are_supported(tmp_path: Path) -> None:
    root = _make_repository(tmp_path, test_body=_file_content_test("VALUE = 2"))
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = _local_verifier().verify(
        _task("python -m pytest -q", "python -m ruff check ."),
        workspace=LocalGitWorkspace(root),
    )

    assert result.passed is True
    assert [check.name for check in result.checks[1:]] == ["pytest", "ruff"]
