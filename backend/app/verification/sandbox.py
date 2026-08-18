from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Protocol, runtime_checkable
from uuid import uuid4

from app.models.failure import FailureType
from app.models.sandbox import DockerSandboxPolicy
from app.models.verification import VerificationBackend


@dataclass(frozen=True)
class VerificationExecution:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    backend: VerificationBackend
    details: tuple[str, ...] = ()
    failure_type: FailureType | None = None


@runtime_checkable
class VerificationCommandRunner(Protocol):
    @property
    def is_sandboxed(self) -> bool:
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> VerificationExecution:
        ...


class LocalProcessVerificationRunner:
    """Host-process runner retained only for focused unit tests and migration checks.

    Production composition must use ``DockerSandboxRunner``. Custom project commands are rejected
    by ``DeterministicVerifier`` when this non-sandboxed runner is selected.
    """

    @property
    def is_sandboxed(self) -> bool:
        return False

    def run(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> VerificationExecution:
        command = list(argv)
        if command and command[0] == "python":
            command[0] = sys.executable

        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        started_at = perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            return VerificationExecution(
                exit_code=None,
                stdout=_timeout_text(exc.stdout),
                stderr=_timeout_text(exc.stderr)
                or f"Verification command timed out after {timeout_seconds:.2f} seconds.",
                duration_ms=_duration_ms(started_at),
                backend=VerificationBackend.HOST,
                details=("sandboxed=false",),
                failure_type=FailureType.TOOL_FAILURE,
            )
        except OSError as exc:
            return VerificationExecution(
                exit_code=None,
                stdout="",
                stderr=f"Unable to start verification command: {exc}",
                duration_ms=_duration_ms(started_at),
                backend=VerificationBackend.HOST,
                details=("sandboxed=false",),
                failure_type=FailureType.TOOL_FAILURE,
            )

        return VerificationExecution(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=_duration_ms(started_at),
            backend=VerificationBackend.HOST,
            details=("sandboxed=false",),
        )


class DockerSandboxRunner:
    """Run untrusted verification commands in a bounded, read-only Docker container."""

    _CONTAINER_WORKSPACE = "/workspace"
    _CLEANUP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        policy: DockerSandboxPolicy | None = None,
        *,
        docker_executable: str = "docker",
    ) -> None:
        if not docker_executable or any(character.isspace() for character in docker_executable):
            raise ValueError("docker_executable must be one non-empty executable token")
        self._policy = policy or DockerSandboxPolicy()
        self._docker_executable = docker_executable

    @property
    def is_sandboxed(self) -> bool:
        return True

    @property
    def policy(self) -> DockerSandboxPolicy:
        return self._policy

    def run(
        self,
        argv: Sequence[str],
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> VerificationExecution:
        if not argv:
            return self._tool_failure("Sandbox command must not be empty.")
        if timeout_seconds <= 0:
            return self._tool_failure("Sandbox timeout must be greater than zero.")

        root = workspace.resolve()
        if not root.is_dir():
            return self._tool_failure("Sandbox workspace must be an existing directory.")
        if "," in str(root):
            return self._tool_failure(
                "Sandbox workspace path cannot contain a comma when using Docker --mount."
            )

        container_name = f"devflow-verify-{uuid4().hex}"
        command = self._docker_run_command(
            argv=argv,
            workspace=root,
            container_name=container_name,
        )
        started_at = perf_counter()
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            cleanup_error = self._force_remove(container_name)
            stderr = _timeout_text(exc.stderr) or (
                f"Docker verification sandbox timed out after {timeout_seconds:.2f} seconds."
            )
            if cleanup_error:
                stderr = f"{stderr}\nSandbox cleanup warning: {cleanup_error}"
            return VerificationExecution(
                exit_code=None,
                stdout=_timeout_text(exc.stdout),
                stderr=stderr,
                duration_ms=_duration_ms(started_at),
                backend=VerificationBackend.DOCKER,
                details=self._execution_details(container_name),
                failure_type=FailureType.SANDBOX_TIMEOUT,
            )
        except OSError as exc:
            return VerificationExecution(
                exit_code=None,
                stdout="",
                stderr=f"Unable to start Docker verification sandbox: {exc}",
                duration_ms=_duration_ms(started_at),
                backend=VerificationBackend.DOCKER,
                details=self._execution_details(container_name),
                failure_type=FailureType.TOOL_FAILURE,
            )

        failure_type = None
        stderr = completed.stderr
        if completed.returncode in {125, 126, 127}:
            failure_type = FailureType.TOOL_FAILURE
            stderr = stderr or (
                "Docker could not create/start the verification container or its command."
            )
        elif completed.returncode == 137:
            failure_type = FailureType.TOOL_FAILURE
            stderr = stderr or (
                "Verification sandbox was killed, commonly because a configured resource limit "
                "was exceeded."
            )

        return VerificationExecution(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=stderr,
            duration_ms=_duration_ms(started_at),
            backend=VerificationBackend.DOCKER,
            details=self._execution_details(container_name),
            failure_type=failure_type,
        )

    def _docker_run_command(
        self,
        *,
        argv: Sequence[str],
        workspace: Path,
        container_name: str,
    ) -> list[str]:
        policy = self._policy
        memory = f"{policy.memory_mb}m"
        return [
            self._docker_executable,
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull",
            policy.pull_policy,
            "--network",
            policy.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--user",
            policy.container_user,
            "--cpus",
            f"{policy.cpus:g}",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--pids-limit",
            str(policy.pids_limit),
            "--shm-size",
            f"{policy.shm_mb}m",
            "--tmpfs",
            f"/tmp:rw,nosuid,nodev,size={policy.tmpfs_mb}m,mode=1777",
            "--mount",
            (
                "type=bind,"
                f"src={workspace},"
                f"dst={self._CONTAINER_WORKSPACE},"
                "readonly"
            ),
            "--workdir",
            self._CONTAINER_WORKSPACE,
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "HOME=/tmp/home",
            "--env",
            "TMPDIR=/tmp",
            "--env",
            "XDG_CACHE_HOME=/tmp/cache",
            policy.image,
            *argv,
        ]

    def _execution_details(self, container_name: str) -> tuple[str, ...]:
        policy = self._policy
        return (
            f"container={container_name}",
            f"image={policy.image}",
            "workspace=readonly",
            "rootfs=readonly",
            f"network={policy.network}",
            f"cpus={policy.cpus:g}",
            f"memory_mb={policy.memory_mb}",
            f"pids_limit={policy.pids_limit}",
            f"tmpfs_mb={policy.tmpfs_mb}",
            "capabilities=none",
            "no_new_privileges=true",
            f"user={policy.container_user}",
        )

    def _force_remove(self, container_name: str) -> str:
        try:
            completed = subprocess.run(
                [self._docker_executable, "rm", "-f", container_name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._CLEANUP_TIMEOUT_SECONDS,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return str(exc)
        if completed.returncode == 0:
            return ""
        return (completed.stderr or completed.stdout).strip() or (
            f"docker rm -f exited with {completed.returncode}"
        )

    def _tool_failure(self, message: str) -> VerificationExecution:
        return VerificationExecution(
            exit_code=None,
            stdout="",
            stderr=message,
            duration_ms=0,
            backend=VerificationBackend.DOCKER,
            details=(f"image={self._policy.image}", "sandboxed=true"),
            failure_type=FailureType.TOOL_FAILURE,
        )


def _duration_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
