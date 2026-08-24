from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from app.models.failure import FailureType
from app.models.sandbox import DockerSandboxPolicy
from app.models.verification import VerificationBackend
from app.verification.sandbox import (
    DockerSandboxRunner,
    VerificationCommandRunner,
    VerificationExecution,
)

_PINNED_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$"
)


class ProjectVerificationKind(StrEnum):
    PYTHON_BASE = "PYTHON_BASE"
    PYTHON_PINNED_REQUIREMENTS = "PYTHON_PINNED_REQUIREMENTS"
    NODE_NPM_LOCK = "NODE_NPM_LOCK"


class ProjectVerificationEnvironmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectVerificationProfile:
    kind: ProjectVerificationKind
    manifest_paths: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class DependencyMount:
    source: Path
    target: str
    environment: tuple[tuple[str, str], ...]
    profile: ProjectVerificationProfile


class ProjectVerificationProfileResolver:
    """Select only verification environments that can be constructed deterministically."""

    def resolve(self, workspace: Path) -> ProjectVerificationProfile:
        root = workspace.resolve()
        package_json = root / "package.json"
        package_lock = root / "package-lock.json"
        requirements = root / "requirements.txt"
        pyproject = root / "pyproject.toml"
        uv_lock = root / "uv.lock"

        if package_json.is_file():
            if not package_lock.is_file():
                raise ProjectVerificationEnvironmentError(
                    "Node project requires package-lock.json for deterministic verification"
                )
            return self._profile(
                root,
                ProjectVerificationKind.NODE_NPM_LOCK,
                ("package.json", "package-lock.json"),
            )

        if requirements.is_file():
            self._validate_pinned_requirements(requirements)
            return self._profile(
                root,
                ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS,
                ("requirements.txt",),
            )

        if uv_lock.is_file():
            raise ProjectVerificationEnvironmentError(
                "uv.lock was detected but this release does not yet build a uv dependency cache; "
                "export a fully pinned requirements.txt or configure a supported profile"
            )
        if pyproject.is_file():
            raise ProjectVerificationEnvironmentError(
                "Python project has pyproject.toml without a supported locked dependency profile"
            )
        return self._profile(root, ProjectVerificationKind.PYTHON_BASE, ())

    @staticmethod
    def _validate_pinned_requirements(path: Path) -> None:
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("--", "-r", "-c", "git+", "http://", "https://")):
                raise ProjectVerificationEnvironmentError(
                    f"requirements.txt line {line_number} is not a bounded pinned package"
                )
            if _PINNED_REQUIREMENT_RE.fullmatch(line) is None:
                raise ProjectVerificationEnvironmentError(
                    f"requirements.txt line {line_number} must pin one package with =="
                )

    @staticmethod
    def _profile(
        root: Path,
        kind: ProjectVerificationKind,
        manifest_paths: tuple[str, ...],
    ) -> ProjectVerificationProfile:
        digest = hashlib.sha256()
        digest.update(kind.value.encode())
        for relative in manifest_paths:
            digest.update(relative.encode())
            digest.update((root / relative).read_bytes())
        return ProjectVerificationProfile(
            kind=kind,
            manifest_paths=manifest_paths,
            digest=digest.hexdigest(),
        )


class ProjectDependencyCacheBuilder:
    """Build dependency-only host caches before the networkless authoritative verification run."""

    def __init__(
        self,
        *,
        root: Path,
        python_image: str,
        node_image: str,
        build_timeout_seconds: float = 300.0,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._python_image = python_image
        self._node_image = node_image
        self._build_timeout_seconds = build_timeout_seconds

    def ensure(
        self,
        workspace: Path,
        profile: ProjectVerificationProfile,
    ) -> DependencyMount | None:
        if profile.kind is ProjectVerificationKind.PYTHON_BASE:
            return None
        final = self._root / profile.digest
        marker = final / ".complete"
        if marker.is_file():
            return self._mount(final, profile)

        temporary = self._root / f".{profile.digest}.tmp-{uuid4().hex}"
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            with tempfile.TemporaryDirectory(prefix="devflow-verification-manifest-") as temp_dir:
                manifests = Path(temp_dir)
                for relative in profile.manifest_paths:
                    shutil.copy2(workspace / relative, manifests / Path(relative).name)
                if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS:
                    self._build_python(manifests, temporary)
                elif profile.kind is ProjectVerificationKind.NODE_NPM_LOCK:
                    self._build_node(manifests, temporary)
                else:
                    raise ProjectVerificationEnvironmentError(
                        f"unsupported verification profile: {profile.kind.value}"
                    )
            (temporary / ".complete").write_text(profile.kind.value, encoding="utf-8")
            try:
                os.replace(temporary, final)
            except OSError:
                if not marker.is_file():
                    raise
                shutil.rmtree(temporary, ignore_errors=True)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        return self._mount(final, profile)

    def _build_python(self, manifests: Path, output: Path) -> None:
        self._run_dependency_container(
            image=self._python_image,
            manifests=manifests,
            output=output,
            command=[
                "python",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--target",
                "/deps",
                "-r",
                "/manifest/requirements.txt",
            ],
        )

    def _build_node(self, manifests: Path, output: Path) -> None:
        script = (
            "set -eu; mkdir -p /tmp/devflow-node; "
            "cp /manifest/package.json /manifest/package-lock.json /tmp/devflow-node/; "
            "cd /tmp/devflow-node; npm ci --ignore-scripts --no-audit --no-fund; "
            "cp -a node_modules/. /deps/"
        )
        self._run_dependency_container(
            image=self._node_image,
            manifests=manifests,
            output=output,
            command=["sh", "-lc", script],
        )

    def _run_dependency_container(
        self,
        *,
        image: str,
        manifests: Path,
        output: Path,
        command: list[str],
    ) -> None:
        for path in (manifests, output):
            if "," in str(path):
                raise ProjectVerificationEnvironmentError(
                    "verification dependency cache paths cannot contain commas"
                )
        try:
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "bridge",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges=true",
                    "--mount",
                    f"type=bind,src={manifests},dst=/manifest,readonly",
                    "--mount",
                    f"type=bind,src={output},dst=/deps",
                    "--entrypoint",
                    "",
                    image,
                    *command,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._build_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProjectVerificationEnvironmentError(
                "verification dependency cache build could not complete"
            ) from exc
        if result.returncode != 0:
            raise ProjectVerificationEnvironmentError(
                "verification dependency cache build failed; no test result was inferred"
            )

    @staticmethod
    def _mount(path: Path, profile: ProjectVerificationProfile) -> DependencyMount:
        if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS:
            return DependencyMount(
                source=path,
                target="/opt/devflow-deps",
                environment=(("PYTHONPATH", "/opt/devflow-deps"),),
                profile=profile,
            )
        return DependencyMount(
            source=path,
            target="/workspace/node_modules",
            environment=(
                ("NODE_PATH", "/workspace/node_modules"),
                (
                    "PATH",
                    "/workspace/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                ),
            ),
            profile=profile,
        )


class DependencyMountDockerSandboxRunner(DockerSandboxRunner):
    def __init__(self, policy: DockerSandboxPolicy, mount: DependencyMount) -> None:
        super().__init__(policy)
        self._dependency_mount = mount

    def _docker_run_command(self, **kwargs) -> list[str]:  # type: ignore[override]
        command = super()._docker_run_command(**kwargs)
        image_reference = kwargs["image_reference"]
        image_index = command.index(image_reference)
        extras = [
            "--mount",
            (
                "type=bind,"
                f"src={self._dependency_mount.source},"
                f"dst={self._dependency_mount.target},readonly"
            ),
        ]
        for name, value in self._dependency_mount.environment:
            extras.extend(["--env", f"{name}={value}"])
        return [*command[:image_index], *extras, *command[image_index:]]


class ProjectAwareVerificationRunner(VerificationCommandRunner):
    """Resolve dependency profiles before delegating to the accepted networkless sandbox."""

    def __init__(
        self,
        *,
        base_policy: DockerSandboxPolicy,
        node_image: str,
        cache_root: Path,
    ) -> None:
        self._base_policy = base_policy
        self._node_image = node_image
        self._resolver = ProjectVerificationProfileResolver()
        self._cache = ProjectDependencyCacheBuilder(
            root=cache_root,
            python_image=base_policy.image,
            node_image=node_image,
        )

    @property
    def is_sandboxed(self) -> bool:
        return True

    def run(
        self,
        argv,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> VerificationExecution:
        try:
            profile = self._resolver.resolve(workspace)
            mount = self._cache.ensure(workspace, profile)
        except ProjectVerificationEnvironmentError as exc:
            return VerificationExecution(
                exit_code=None,
                stdout="",
                stderr=str(exc),
                duration_ms=0,
                backend=VerificationBackend.DOCKER,
                details=("project_verification_profile=unavailable",),
                failure_type=FailureType.VERIFICATION_ENV_UNAVAILABLE,
            )

        image = (
            self._node_image
            if profile.kind is ProjectVerificationKind.NODE_NPM_LOCK
            else self._base_policy.image
        )
        policy = self._base_policy.model_copy(update={"image": image})
        runner: DockerSandboxRunner = (
            DockerSandboxRunner(policy)
            if mount is None
            else DependencyMountDockerSandboxRunner(policy, mount)
        )
        return runner.run(argv, workspace=workspace, timeout_seconds=timeout_seconds)
