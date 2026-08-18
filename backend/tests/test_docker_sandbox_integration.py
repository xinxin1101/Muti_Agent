from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.models import FailureType, TaskContract, VerificationBackend
from app.verification import DeterministicVerifier, DockerSandboxRunner
from app.workspace import LocalGitWorkspace


@pytest.fixture(autouse=True)
def require_real_docker_sandbox() -> None:
    preflight = DockerSandboxRunner()._preflight(5.0)
    if not isinstance(preflight, str):
        return
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail(
            "CI must provide the real trusted Docker verification sandbox: "
            f"{preflight}"
        )
    pytest.skip(f"trusted Docker verification sandbox is unavailable locally: {preflight}")


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> LocalGitWorkspace:
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
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    (root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    return LocalGitWorkspace(root)


def _task(*commands: str) -> TaskContract:
    return TaskContract(
        task_id="SANDBOX-INTEGRATION",
        objective="Verify the changed module inside the Docker sandbox.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Verification is isolated and bounded."],
        verification_commands=list(commands),
        max_retries=1,
    )


def test_default_verifier_runs_pytest_inside_real_docker_sandbox(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)

    result = DeterministicVerifier(command_timeout_seconds=10).verify(
        _task("pytest -q"),
        workspace=workspace,
    )

    assert result.passed is True
    check = result.checks[1]
    assert check.execution_backend is VerificationBackend.DOCKER
    assert check.exit_code == 0
    assert "workspace=readonly" in check.execution_details
    assert "rootfs=readonly" in check.execution_details
    assert "network=none" in check.execution_details
    assert "entrypoint=cleared" in check.execution_details
    assert any(detail.startswith("image_id=sha256:") for detail in check.execution_details)
    assert workspace.changed_files() == ["module.py"]
    assert (workspace.root / ".pytest_cache").exists() is False


def test_custom_project_command_is_allowed_only_inside_real_sandbox(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)

    result = DeterministicVerifier(command_timeout_seconds=10).verify(
        _task('python -c "print(\'sandbox-custom-ok\')"'),
        workspace=workspace,
    )

    assert result.passed is True
    check = result.checks[1]
    assert check.name == "custom"
    assert check.execution_backend is VerificationBackend.DOCKER
    assert "sandbox-custom-ok" in check.stdout
    assert any(detail.startswith("image_id=sha256:") for detail in check.execution_details)


def test_read_only_workspace_prevents_untrusted_verification_write(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    marker = workspace.root / "sandbox-write.txt"

    result = DeterministicVerifier(command_timeout_seconds=10).verify(
        _task(
            'python -c "from pathlib import Path; '
            "Path('sandbox-write.txt').write_text('bad', encoding='utf-8')\""
        ),
        workspace=workspace,
    )

    assert result.passed is False
    check = result.checks[1]
    assert check.execution_backend is VerificationBackend.DOCKER
    assert check.failure_type is FailureType.TOOL_FAILURE
    assert check.exit_code not in (None, 0)
    assert marker.exists() is False
    assert workspace.changed_files() == ["module.py"]


def test_network_none_blocks_external_connection(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    command = (
        'python -c "import socket; s=socket.socket(); s.settimeout(0.3); '
        "rc=s.connect_ex(('1.1.1.1', 53)); print('network-blocked', rc); "
        "raise SystemExit(0 if rc != 0 else 9)\""
    )

    result = DeterministicVerifier(command_timeout_seconds=10).verify(
        _task(command),
        workspace=workspace,
    )

    assert result.passed is True
    check = result.checks[1]
    assert check.execution_backend is VerificationBackend.DOCKER
    assert "network-blocked" in check.stdout
    assert "network=none" in check.execution_details


def test_real_sandbox_timeout_is_typed_and_container_is_removed(tmp_path: Path) -> None:
    workspace = _repository(tmp_path)
    verifier = DeterministicVerifier(command_timeout_seconds=0.5)

    result = verifier.verify(
        _task('python -c "import time; time.sleep(5)"'),
        workspace=workspace,
    )

    assert result.passed is False
    check = result.checks[1]
    assert check.failure_type is FailureType.SANDBOX_TIMEOUT
    assert check.execution_backend is VerificationBackend.DOCKER
    assert check.exit_code is None
    reports = verifier.failure_reports(result)
    assert reports[0].failure_type is FailureType.SANDBOX_TIMEOUT
    assert reports[0].retryable is False

    container = next(
        detail.removeprefix("container=")
        for detail in check.execution_details
        if detail.startswith("container=")
    )
    remaining = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name=^{container}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert remaining == ""
