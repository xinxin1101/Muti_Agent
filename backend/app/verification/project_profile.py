from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any
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


class VerificationRuntime(StrEnum):
    """A trusted runtime capability required by one verification command."""

    PYTHON = "PYTHON"
    NODE = "NODE"


_NODE_EXECUTABLES = frozenset({"node", "nodejs", "npm", "npx"})


def verification_runtime_for_argv(argv: Sequence[str]) -> VerificationRuntime:
    """Choose the sandbox runtime from an already tokenized verification command.

    Dependency manifests describe third-party packages, not necessarily the language of a
    generated, dependency-free program.  Keep this decision at the command boundary so a bare
    ``node test/example.js`` cannot accidentally be sent to the Python verifier image.
    """

    if argv and argv[0].strip().lower() in _NODE_EXECUTABLES:
        return VerificationRuntime.NODE
    return VerificationRuntime.PYTHON


def verification_runtime_for_command(command: str) -> VerificationRuntime:
    """Best-effort pre-dispatch runtime inference for a planner-owned command string."""

    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        # Command syntax is authoritatively validated later by DeterministicVerifier.  Do not
        # make preflight hide that bounded validation error behind a runtime guess.
        return VerificationRuntime.PYTHON
    return verification_runtime_for_argv(argv)


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


@dataclass(frozen=True)
class DependencyBuildArtifact:
    """Immutable, credential-free evidence for a reusable dependency cache."""

    root: Path
    profile: ProjectVerificationProfile
    metadata_path: Path
    log_path: Path


@dataclass(frozen=True)
class DependencyCacheCleanup:
    removed_fingerprints: tuple[str, ...]
    reclaimed_bytes: int
    retained_bytes: int


class ProjectVerificationProfileResolver:
    """Select only verification environments that can be constructed deterministically."""

    def resolve(self, workspace: Path) -> ProjectVerificationProfile:
        root = workspace.resolve()
        package_json = root / "package.json"
        package_lock = root / "package-lock.json"
        unsupported_node_locks = tuple(
            name
            for name in ("npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
            if (root / name).is_file()
        )
        requirements = next(
            (
                root / filename
                for filename in ("requirements.txt", "requirements.lock", "requirements-dev.lock")
                if (root / filename).is_file()
            ),
            None,
        )
        pyproject = root / "pyproject.toml"
        uv_lock = root / "uv.lock"
        poetry_lock = root / "poetry.lock"
        pipfile_lock = root / "Pipfile.lock"

        if package_json.is_file():
            if not package_lock.is_file():
                if unsupported_node_locks:
                    lock_names = ", ".join(unsupported_node_locks)
                    raise ProjectVerificationEnvironmentError(
                        f"Node project uses unsupported lock file(s): {lock_names}; "
                        "export package-lock.json or configure a supported profile"
                    )
                raise ProjectVerificationEnvironmentError(
                    "Node project requires package-lock.json for deterministic verification"
                )
            return self._profile(
                root,
                ProjectVerificationKind.NODE_NPM_LOCK,
                ("package.json", "package-lock.json"),
            )
        if package_lock.is_file() or unsupported_node_locks:
            raise ProjectVerificationEnvironmentError(
                "Node lock file was detected without package.json; "
                "the dependency environment cannot be resolved deterministically"
            )

        if requirements is not None:
            self._validate_pinned_requirements(requirements)
            return self._profile(
                root,
                ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS,
                (requirements.name,),
            )

        if uv_lock.is_file():
            raise ProjectVerificationEnvironmentError(
                "uv.lock was detected but this release does not yet build a uv dependency cache; "
                "export a fully pinned requirements.txt or configure a supported profile"
            )
        if poetry_lock.is_file() or pipfile_lock.is_file():
            lock_name = "poetry.lock" if poetry_lock.is_file() else "Pipfile.lock"
            raise ProjectVerificationEnvironmentError(
                f"{lock_name} was detected but this release does not yet build its dependency "
                "cache; export a fully pinned requirements.txt or configure a supported profile"
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
                    f"{path.name} line {line_number} is not a bounded pinned package"
                )
            if _PINNED_REQUIREMENT_RE.fullmatch(line) is None:
                raise ProjectVerificationEnvironmentError(
                    f"{path.name} line {line_number} must pin one package with =="
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
        proxy_url: str | None = None,
        python_index_url: str | None = None,
        node_registry_url: str | None = None,
        max_cache_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._python_image = python_image
        self._node_image = node_image
        self._build_timeout_seconds = build_timeout_seconds
        self._proxy_url = proxy_url
        self._python_index_url = python_index_url
        self._node_registry_url = node_registry_url
        self._max_cache_bytes = max_cache_bytes

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
            self._validate_artifact(final, profile)
            self._record_metric("hits", duration_ms=0)
            return self._mount(final, profile)

        self.cleanup(keep_fingerprints={profile.digest})
        temporary = self._root / f".{profile.digest}.tmp-{uuid4().hex}"
        temporary.mkdir(parents=True, exist_ok=False)
        build_log: list[str] = []
        started_at = perf_counter()
        try:
            with tempfile.TemporaryDirectory(prefix="devflow-verification-manifest-") as temp_dir:
                manifests = Path(temp_dir)
                for relative in profile.manifest_paths:
                    target_name = (
                        "requirements.txt"
                        if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
                        else Path(relative).name
                    )
                    shutil.copy2(workspace / relative, manifests / target_name)
                if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS:
                    self._build_python(manifests, temporary, build_log)
                elif profile.kind is ProjectVerificationKind.NODE_NPM_LOCK:
                    self._build_node(manifests, temporary, build_log)
                else:
                    raise ProjectVerificationEnvironmentError(
                        f"unsupported verification profile: {profile.kind.value}"
                    )
            self._write_artifact_evidence(
                temporary,
                workspace=workspace,
                profile=profile,
                build_log=build_log,
                build_duration_ms=self._duration_ms(started_at),
            )
            (temporary / ".complete").write_text(profile.digest, encoding="utf-8")
            try:
                os.replace(temporary, final)
            except OSError:
                if not marker.is_file():
                    raise
                shutil.rmtree(temporary, ignore_errors=True)
        except Exception:
            self._write_failure_log(profile, build_log)
            self._record_metric("failures", duration_ms=self._duration_ms(started_at))
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        self._record_metric("builds", duration_ms=self._duration_ms(started_at))
        return self._mount(final, profile)

    def _build_python(self, manifests: Path, output: Path, build_log: list[str]) -> None:
        download_command = [
            "python",
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--wheel-dir",
            "/artifact/wheels",
            "-r",
            "/manifest/requirements.txt",
        ]
        if self._python_index_url is not None:
            download_command.extend(("--index-url", self._python_index_url))
        self._run_dependency_container(
            image=self._python_image,
            manifests=manifests,
            output=output,
            command=download_command,
            stage="python-wheel-download",
            build_log=build_log,
        )
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
                "--no-index",
                "--find-links",
                "/artifact/wheels",
                "--target",
                "/artifact/python-site-packages",
                "-r",
                "/manifest/requirements.txt",
            ],
            network="none",
            stage="python-offline-install",
            build_log=build_log,
        )

    def _build_node(self, manifests: Path, output: Path, build_log: list[str]) -> None:
        registry_argument = ""
        if self._node_registry_url is not None:
            registry_argument = f" --registry {shlex.quote(self._node_registry_url)}"
        download_script = (
            "set -eu; mkdir -p /tmp/devflow-node; "
            "cp /manifest/package.json /manifest/package-lock.json /tmp/devflow-node/; "
            "cd /tmp/devflow-node; npm ci --ignore-scripts --no-audit --no-fund"
            f" --cache /artifact/npm-cache{registry_argument}"
        )
        self._run_dependency_container(
            image=self._node_image,
            manifests=manifests,
            output=output,
            command=["sh", "-lc", download_script],
            stage="node-npm-download",
            build_log=build_log,
        )
        offline_script = (
            "set -eu; mkdir -p /tmp/devflow-node /artifact/node_modules; "
            "cp /manifest/package.json /manifest/package-lock.json /tmp/devflow-node/; "
            "cd /tmp/devflow-node; npm ci --offline --ignore-scripts --no-audit --no-fund "
            "--cache /artifact/npm-cache; cp -a node_modules/. /artifact/node_modules/"
        )
        self._run_dependency_container(
            image=self._node_image,
            manifests=manifests,
            output=output,
            command=["sh", "-lc", offline_script],
            network="none",
            stage="node-offline-install",
            build_log=build_log,
        )

    def _run_dependency_container(
        self,
        *,
        image: str,
        manifests: Path,
        output: Path,
        command: list[str],
        stage: str,
        build_log: list[str],
        network: str = "bridge",
    ) -> None:
        for path in (manifests, output):
            if "," in str(path):
                raise ProjectVerificationEnvironmentError(
                    "verification dependency cache paths cannot contain commas"
                )
        try:
            docker_command = [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges=true",
            ]
            if self._proxy_url is not None:
                docker_command.extend(
                    (
                        "--env",
                        f"HTTP_PROXY={self._proxy_url}",
                        "--env",
                        f"HTTPS_PROXY={self._proxy_url}",
                        "--env",
                        "NO_PROXY=localhost,127.0.0.1",
                    )
                )
            docker_command.extend(
                (
                    "--mount",
                    f"type=bind,src={manifests},dst=/manifest,readonly",
                    "--mount",
                    f"type=bind,src={output},dst=/artifact",
                    "--entrypoint",
                    "",
                    image,
                    *command,
                )
            )
            result = subprocess.run(
                docker_command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._build_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            build_log.append(f"stage={stage}\nerror={type(exc).__name__}: {exc}\n")
            raise ProjectVerificationEnvironmentError(
                "verification dependency cache build could not complete"
            ) from exc
        build_log.append(self._render_build_log(stage, network, result))
        if result.returncode != 0:
            raise ProjectVerificationEnvironmentError(
                "verification dependency cache build failed; no test result was inferred"
            )

    def _write_artifact_evidence(
        self,
        output: Path,
        *,
        workspace: Path,
        profile: ProjectVerificationProfile,
        build_log: list[str],
        build_duration_ms: int,
    ) -> DependencyBuildArtifact:
        log_path = output / "build.log"
        log_path.write_text("\n".join(build_log), encoding="utf-8")
        manifest_records = tuple(
            {
                "path": relative,
                "sha256": hashlib.sha256((workspace / relative).read_bytes()).hexdigest(),
            }
            for relative in profile.manifest_paths
        )
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "dependency_fingerprint": profile.digest,
            "profile_kind": profile.kind.value,
            "manifest_records": manifest_records,
            "dependency_manifest": self._dependency_manifest(workspace, profile),
            "toolchain": {
                "image": (
                    self._python_image
                    if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
                    else self._node_image
                ),
                "build_timeout_seconds": self._build_timeout_seconds,
            },
            "cache_layout": (
                "wheels+python-site-packages"
                if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
                else "npm-cache+node_modules"
            ),
            "download_network": "bridge",
            "verification_network": "none",
            "build_duration_ms": build_duration_ms,
            "build_log_sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
        }
        metadata_path = output / "artifact.json"
        metadata_path.write_text(
            json.dumps(metadata, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return DependencyBuildArtifact(
            root=output,
            profile=profile,
            metadata_path=metadata_path,
            log_path=log_path,
        )

    def artifact(self, profile: ProjectVerificationProfile) -> DependencyBuildArtifact | None:
        root = self._root / profile.digest
        if not (root / ".complete").is_file():
            return None
        self._validate_artifact(root, profile)
        return DependencyBuildArtifact(
            root=root,
            profile=profile,
            metadata_path=root / "artifact.json",
            log_path=root / "build.log",
        )

    def failure_log(self, profile: ProjectVerificationProfile) -> Path | None:
        path = self._root / f"{profile.digest}.last-failure.log"
        return path if path.is_file() else None

    def rebuild(
        self,
        workspace: Path,
        profile: ProjectVerificationProfile,
    ) -> DependencyMount:
        target = self._root / profile.digest
        if target.is_dir():
            shutil.rmtree(target)
        mount = self.ensure(workspace, profile)
        if mount is None:
            raise ProjectVerificationEnvironmentError("base Python profile has no cache to rebuild")
        return mount

    def cleanup(
        self,
        *,
        keep_fingerprints: set[str] | None = None,
        max_cache_bytes: int | None = None,
    ) -> DependencyCacheCleanup:
        limit = self._max_cache_bytes if max_cache_bytes is None else max_cache_bytes
        keep = keep_fingerprints or set()
        candidates = [
            path
            for path in self._root.iterdir()
            if path.is_dir()
            and re.fullmatch(r"[0-9a-f]{64}", path.name) is not None
            and path.name not in keep
        ]
        total = sum(self._directory_size(path) for path in candidates)
        retained = total + sum(
            self._directory_size(self._root / fingerprint)
            for fingerprint in keep
            if (self._root / fingerprint).is_dir()
        )
        removed: list[str] = []
        reclaimed = 0
        for path in sorted(candidates, key=lambda item: item.stat().st_mtime):
            if retained <= limit:
                break
            size = self._directory_size(path)
            shutil.rmtree(path)
            removed.append(path.name)
            reclaimed += size
            retained -= size
        return DependencyCacheCleanup(
            removed_fingerprints=tuple(removed),
            reclaimed_bytes=reclaimed,
            retained_bytes=max(0, retained),
        )

    def metrics(self) -> dict[str, int]:
        defaults = {"hits": 0, "builds": 0, "failures": 0, "build_duration_ms_total": 0}
        path = self._root / "cache-metrics.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults
        return {
            key: value if isinstance(value := document.get(key), int) and value >= 0 else default
            for key, default in defaults.items()
        }

    def _record_metric(self, kind: str, *, duration_ms: int) -> None:
        metrics = self.metrics()
        metrics[kind] = metrics.get(kind, 0) + 1
        if kind == "builds":
            metrics["build_duration_ms_total"] += duration_ms
        temporary = self._root / f".cache-metrics-{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(metrics, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self._root / "cache-metrics.json")

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))

    def _dependency_manifest(
        self,
        workspace: Path,
        profile: ProjectVerificationProfile,
    ) -> dict[str, Any]:
        if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS:
            source = workspace / profile.manifest_paths[0]
            packages = tuple(
                line.strip()
                for line in source.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )
            return {"package_manager": "pip", "locked_packages": packages}
        package_json = json.loads((workspace / "package.json").read_text(encoding="utf-8"))
        lock_document = json.loads((workspace / "package-lock.json").read_text(encoding="utf-8"))
        declarations: dict[str, str] = {}
        if isinstance(package_json, dict):
            for group in ("dependencies", "devDependencies", "optionalDependencies"):
                values = package_json.get(group)
                if isinstance(values, dict):
                    declarations.update(
                        {
                            name: self._redact(version)
                            for name, version in values.items()
                            if isinstance(name, str) and isinstance(version, str)
                        }
                    )
        return {
            "package_manager": "npm",
            "lockfile_version": lock_document.get("lockfileVersion"),
            "locked_packages": dict(sorted(declarations.items())),
        }

    def _write_failure_log(
        self,
        profile: ProjectVerificationProfile,
        build_log: list[str],
    ) -> None:
        if not build_log:
            return
        path = self._root / f"{profile.digest}.last-failure.log"
        path.write_text("\n".join(build_log), encoding="utf-8")

    def _render_build_log(
        self,
        stage: str,
        network: str,
        result: subprocess.CompletedProcess[str],
    ) -> str:
        return (
            f"stage={stage}\nnetwork={network}\nexit_code={result.returncode}\n"
            f"stdout={self._redact(result.stdout)[-4_000:]}\n"
            f"stderr={self._redact(result.stderr)[-4_000:]}\n"
        )

    def _validate_artifact(
        self,
        root: Path,
        profile: ProjectVerificationProfile,
    ) -> None:
        metadata_path = root / "artifact.json"
        log_path = root / "build.log"
        expected_paths = (
            (root / "wheels", root / "python-site-packages")
            if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
            else (root / "npm-cache", root / "node_modules")
        )
        if (
            not metadata_path.is_file()
            or not log_path.is_file()
            or not all(path.is_dir() for path in expected_paths)
        ):
            raise ProjectVerificationEnvironmentError(
                "dependency cache artifact is incomplete; "
                "delete the matching cache entry and rebuild"
            )
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectVerificationEnvironmentError(
                "dependency cache artifact metadata is unreadable"
            ) from exc
        if (
            metadata.get("dependency_fingerprint") != profile.digest
            or metadata.get("profile_kind") != profile.kind.value
            or metadata.get("build_log_sha256") != hashlib.sha256(log_path.read_bytes()).hexdigest()
        ):
            raise ProjectVerificationEnvironmentError(
                "dependency cache artifact integrity validation failed"
            )

    def _redact(self, value: str) -> str:
        redacted = value
        if self._proxy_url is not None:
            redacted = redacted.replace(self._proxy_url, "[REDACTED_PROXY]")
        redacted = re.sub(r"https?://[^\s/@]+@", "https://[REDACTED]@", redacted)
        return re.sub(
            r"(?i)\b(authorization|token|password|api[_-]?key)\s*[:=]\s*\S+",
            r"\1=[REDACTED]",
            redacted,
        )

    @staticmethod
    def _mount(path: Path, profile: ProjectVerificationProfile) -> DependencyMount:
        if profile.kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS:
            return DependencyMount(
                source=path / "python-site-packages",
                target="/opt/devflow-deps",
                environment=(("PYTHONPATH", "/opt/devflow-deps"),),
                profile=profile,
            )
        return DependencyMount(
            source=path / "node_modules",
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
        proxy_url: str | None = None,
        python_index_url: str | None = None,
        node_registry_url: str | None = None,
        build_timeout_seconds: float = 300.0,
        max_cache_bytes: int = 5 * 1024 * 1024 * 1024,
    ) -> None:
        self._base_policy = base_policy
        self._node_image = node_image
        self._resolver = ProjectVerificationProfileResolver()
        self._cache = ProjectDependencyCacheBuilder(
            root=cache_root,
            python_image=base_policy.image,
            node_image=node_image,
            proxy_url=proxy_url,
            python_index_url=python_index_url,
            node_registry_url=node_registry_url,
            build_timeout_seconds=build_timeout_seconds,
            max_cache_bytes=max_cache_bytes,
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

        runtime = verification_runtime_for_argv(argv)
        image = self._node_image if runtime is VerificationRuntime.NODE else self._base_policy.image
        policy = self._base_policy.model_copy(update={"image": image})
        runner: DockerSandboxRunner = (
            DockerSandboxRunner(policy)
            if mount is None
            else DependencyMountDockerSandboxRunner(policy, mount)
        )
        execution = runner.run(argv, workspace=workspace, timeout_seconds=timeout_seconds)
        return self._normalize_runtime_failure(execution, runtime)

    @staticmethod
    def _normalize_runtime_failure(
        execution: VerificationExecution,
        runtime: VerificationRuntime,
    ) -> VerificationExecution:
        """Turn a missing interpreter into an actionable environment diagnosis.

        Pre-dispatch capability checks cover normal launches.  This remains a fail-closed guard
        for an image removed or replaced between preflight and verification.
        """

        if execution.failure_type is not FailureType.TOOL_FAILURE:
            return execution
        stderr = execution.stderr.lower()
        executable = "node" if runtime is VerificationRuntime.NODE else "python"
        image_missing = "configured verification image is unavailable" in stderr
        executable_missing = "executable file not found" in stderr and executable in stderr
        if not (image_missing or executable_missing):
            return execution
        label = "Node.js" if runtime is VerificationRuntime.NODE else "Python"
        detail = execution.stderr.strip() or "Docker 未返回额外诊断信息。"
        return replace(
            execution,
            stderr=f"验证环境缺少 {label} 运行时。{detail}",
            details=(
                *execution.details,
                f"verification_runtime={runtime.value}",
                f"runtime_capability={executable}=unavailable",
            ),
            failure_type=FailureType.VERIFICATION_ENV_UNAVAILABLE,
        )
