from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import DockerSandboxPolicy, FailureType, VerificationBackend
from app.verification import DockerSandboxRunner

_IMAGE_ID = "sha256:" + "a" * 64


def _preflight_evidence():
    return SimpleNamespace(image_id=_IMAGE_ID, cgroup_driver="systemd")


def test_policy_rejects_option_like_or_whitespace_image() -> None:
    with pytest.raises(ValueError, match="option prefix"):
        DockerSandboxPolicy(image="--privileged")
    with pytest.raises(ValueError, match="whitespace"):
        DockerSandboxPolicy(image="unsafe image:latest")


def test_docker_run_command_contains_non_optional_security_boundaries(tmp_path: Path) -> None:
    policy = DockerSandboxPolicy(
        image="trusted-verifier:test",
        cpus=0.5,
        memory_mb=256,
        pids_limit=64,
        tmpfs_mb=96,
        shm_mb=32,
    )
    runner = DockerSandboxRunner(policy)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    expected_user = "65532:65532"
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if callable(getuid) and callable(getgid) and getuid() > 0:
        expected_user = f"{getuid()}:{getgid()}"

    command = runner._docker_run_command(
        argv=["python", "-c", "print('ok')"],
        workspace=workspace.resolve(),
        container_name="devflow-verify-test",
        image_reference=_IMAGE_ID,
        container_user=expected_user,
    )

    assert command[:2] == ["docker", "run"]
    assert "--rm" in command
    assert command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges=true"
    assert command[command.index("--user") + 1] == expected_user
    assert command[command.index("--cpus") + 1] == "0.5"
    assert command[command.index("--memory") + 1] == "256m"
    assert command[command.index("--memory-swap") + 1] == "256m"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert command[command.index("--shm-size") + 1] == "32m"
    assert command[command.index("--pull") + 1] == "never"
    assert command[command.index("--entrypoint") + 1] == ""
    mount = command[command.index("--mount") + 1]
    assert f"src={workspace.resolve()}" in mount
    assert "dst=/workspace" in mount
    assert mount.endswith("readonly")
    assert "trusted-verifier:test" not in command
    image_index = command.index(_IMAGE_ID)
    assert command[image_index + 1 :] == ["python", "-c", "print('ok')"]


def test_preflight_resolves_configured_tag_to_immutable_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerSandboxRunner(DockerSandboxPolicy(image="trusted-verifier:test"))
    calls: list[list[str]] = []

    def fake_control(arguments, *, timeout_seconds):
        del timeout_seconds
        calls.append(arguments)
        if arguments[0] == "info":
            return subprocess.CompletedProcess(arguments, 0, '["name=seccomp"]|systemd\n', "")
        return subprocess.CompletedProcess(arguments, 0, f"{_IMAGE_ID}\n", "")

    monkeypatch.setattr(runner, "_control_command", fake_control)
    preflight = runner._preflight(1.0)

    assert not isinstance(preflight, str)
    assert preflight.image_id == _IMAGE_ID
    assert preflight.cgroup_driver == "systemd"
    assert calls[1][-1] == "trusted-verifier:test"


def test_rootless_daemon_is_rejected_before_untrusted_command_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerSandboxRunner()

    def fake_control(arguments, *, timeout_seconds):
        del timeout_seconds
        assert arguments[0] == "info"
        return subprocess.CompletedProcess(
            arguments,
            0,
            '["name=seccomp","name=rootless"]|systemd\n',
            "",
        )

    monkeypatch.setattr(runner, "_control_command", fake_control)

    result = runner._preflight(1.0)
    assert isinstance(result, str)
    assert "Rootless Docker is refused" in result


def test_missing_cgroup_driver_is_rejected_before_untrusted_command_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerSandboxRunner()

    def fake_control(arguments, *, timeout_seconds):
        del timeout_seconds
        assert arguments[0] == "info"
        return subprocess.CompletedProcess(arguments, 0, '["name=seccomp"]|none\n', "")

    monkeypatch.setattr(runner, "_control_command", fake_control)

    result = runner._preflight(1.0)
    assert isinstance(result, str)
    assert "no usable cgroup driver" in result


def test_timeout_forces_container_removal_and_returns_sandbox_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    calls: list[list[str]] = []
    runner = DockerSandboxRunner()
    monkeypatch.setattr(runner, "_preflight", lambda timeout: _preflight_evidence())

    def fake_run(command, **kwargs):
        del kwargs
        calls.append(list(command))
        if command[1] == "run":
            raise subprocess.TimeoutExpired(command, timeout=0.1, output="partial")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.run(
        ["python", "-c", "import time; time.sleep(10)"],
        workspace=workspace,
        timeout_seconds=0.1,
    )

    assert result.backend is VerificationBackend.DOCKER
    assert result.failure_type is FailureType.SANDBOX_TIMEOUT
    assert result.exit_code is None
    assert result.stdout == "partial"
    assert f"image_id={_IMAGE_ID}" in result.details
    assert "entrypoint=cleared" in result.details
    assert len(calls) == 2
    assert calls[1][:3] == ["docker", "rm", "-f"]
    assert calls[1][3].startswith("devflow-verify-")


def test_missing_docker_fails_closed_without_host_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()

    def missing_docker(command, **kwargs):
        del command, kwargs
        raise FileNotFoundError("docker not installed")

    monkeypatch.setattr(subprocess, "run", missing_docker)
    result = DockerSandboxRunner().run(
        ["python", "-c", "print('never')"],
        workspace=workspace,
        timeout_seconds=1.0,
    )

    assert result.failure_type is FailureType.TOOL_FAILURE
    assert result.exit_code is None
    assert "Docker executable is unavailable" in result.stderr


def test_docker_start_failure_is_not_misclassified_as_project_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runner = DockerSandboxRunner()
    monkeypatch.setattr(runner, "_preflight", lambda timeout: _preflight_evidence())

    def fake_run(command, **kwargs):
        del kwargs
        return subprocess.CompletedProcess(command, 125, "", "image not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = runner.run(
        ["python", "-m", "pytest"],
        workspace=workspace,
        timeout_seconds=1.0,
    )

    assert result.failure_type is FailureType.TOOL_FAILURE
    assert result.exit_code == 125
    assert "image not found" in result.stderr
    assert f"image_id={_IMAGE_ID}" in result.details
