from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field

from app.verification.project_profile import (
    DependencyCacheCleanup,
    ProjectDependencyCacheBuilder,
    ProjectVerificationEnvironmentError,
    ProjectVerificationKind,
    ProjectVerificationProfileResolver,
    VerificationRuntime,
    verification_runtime_for_command,
)

_PYTHON_MANIFESTS: Final[tuple[str, ...]] = (
    "requirements.txt",
    "requirements.lock",
    "requirements-dev.lock",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
)
_NODE_MANIFESTS: Final[tuple[str, ...]] = (
    "package.json",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)
_MAX_PACKAGE_PROBES: Final[int] = 8


class DependencyPackageManager(StrEnum):
    NONE = "NONE"
    PYTHON = "PYTHON"
    NODE = "NODE"
    MIXED = "MIXED"


class DependencyPreflightFailureCode(StrEnum):
    UNSUPPORTED_PROFILE = "UNSUPPORTED_PROFILE"
    MIXED_PROJECT = "MIXED_PROJECT"
    DOCKER_UNAVAILABLE = "DOCKER_UNAVAILABLE"
    VERIFIER_IMAGE_UNAVAILABLE = "VERIFIER_IMAGE_UNAVAILABLE"
    VERIFIER_RUNTIME_UNAVAILABLE = "VERIFIER_RUNTIME_UNAVAILABLE"
    CACHE_UNAVAILABLE = "CACHE_UNAVAILABLE"
    REGISTRY_UNREACHABLE = "REGISTRY_UNREACHABLE"
    PACKAGE_UNAVAILABLE = "PACKAGE_UNAVAILABLE"
    DEPENDENCY_INSTALL_FAILED = "DEPENDENCY_INSTALL_FAILED"


class DependencyPreflightReport(BaseModel):
    """Server-derived dependency facts that are safe to return before Run creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_kind: ProjectVerificationKind
    dependency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_manager: DependencyPackageManager
    manifest_paths: tuple[str, ...]
    packages: tuple[str, ...]
    cache_state: str = Field(pattern=r"^(HIT|MISS|NOT_REQUIRED)$")
    docker_version: str = Field(min_length=1, max_length=128)
    registry_url: str | None = Field(default=None, max_length=512)
    proxy_configured: bool
    required_runtimes: tuple[VerificationRuntime, ...] = Field(min_length=1, max_length=2)


class DependencyEnvironmentStatus(BaseModel):
    """Credential-free state for the product environment controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dependency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_kind: ProjectVerificationKind
    package_manager: DependencyPackageManager
    cache_state: str = Field(pattern=r"^(HIT|MISS|NOT_REQUIRED)$")
    artifact_bytes: int = Field(ge=0)
    build_duration_ms: int | None = Field(default=None, ge=0)
    log_tail: str = Field(max_length=8_000)


class DependencyEnvironmentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cache_hits: int = Field(ge=0)
    builds: int = Field(ge=0)
    failures: int = Field(ge=0)
    hit_rate: float = Field(ge=0.0, le=1.0)
    average_build_duration_ms: int = Field(ge=0)
    cache_bytes: int = Field(ge=0)


class DependencyEnvironmentPreflightError(RuntimeError):
    """A bounded, user-safe reason that prevents work from being dispatched."""

    def __init__(
        self,
        *,
        code: DependencyPreflightFailureCode,
        package_manager: DependencyPackageManager,
        manifest_paths: tuple[str, ...],
        packages: tuple[str, ...],
        reason: str,
    ) -> None:
        self.code = code
        self.package_manager = package_manager
        self.manifest_paths = manifest_paths
        self.packages = packages
        self.reason = reason
        super().__init__(self.public_detail)

    @property
    def public_detail(self) -> str:
        package_detail = ", ".join(self.packages[:8]) or "未发现具体第三方包"
        manifest_detail = ", ".join(self.manifest_paths) or "未发现依赖清单"
        return (
            "依赖环境准备失败"
            f"（{self.code.value}，包管理器：{self.package_manager.value}）。"
            f"依赖清单：{manifest_detail}；相关包：{package_detail}。"
            f"原因：{self.reason}"
        )


class _ManifestFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    package_manager: DependencyPackageManager
    manifest_paths: tuple[str, ...]
    packages: tuple[str, ...]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DependencyManifestInspector:
    """Read only direct project manifests; never infer dependencies from source code."""

    def inspect(self, workspace: Path) -> _ManifestFacts:
        root = workspace.resolve()
        python_paths = tuple(name for name in _PYTHON_MANIFESTS if (root / name).is_file())
        node_paths = tuple(name for name in _NODE_MANIFESTS if (root / name).is_file())
        manifest_paths = (*python_paths, *node_paths)
        if python_paths and node_paths:
            manager = DependencyPackageManager.MIXED
        elif python_paths:
            manager = DependencyPackageManager.PYTHON
        elif node_paths:
            manager = DependencyPackageManager.NODE
        else:
            manager = DependencyPackageManager.NONE
        packages = tuple(
            sorted(
                {
                    *self._python_packages(root, python_paths),
                    *self._node_packages(root, node_paths),
                }
            )
        )
        digest = hashlib.sha256()
        digest.update(manager.value.encode("utf-8"))
        for relative in manifest_paths:
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update((root / relative).read_bytes())
            digest.update(b"\0")
        return _ManifestFacts(
            package_manager=manager,
            manifest_paths=manifest_paths,
            packages=packages,
            fingerprint=digest.hexdigest(),
        )

    @staticmethod
    def _python_packages(root: Path, manifests: tuple[str, ...]) -> tuple[str, ...]:
        selected = next(
            (
                name
                for name in ("requirements.txt", "requirements.lock", "requirements-dev.lock")
                if name in manifests
            ),
            None,
        )
        if selected is None:
            return ()
        packages: list[str] = []
        for raw in (root / selected).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-")) or "==" not in line:
                continue
            name = line.split("==", maxsplit=1)[0].split("[", maxsplit=1)[0].strip()
            if name:
                packages.append(name)
        return tuple(packages)

    @staticmethod
    def _node_packages(root: Path, manifests: tuple[str, ...]) -> tuple[str, ...]:
        if "package.json" not in manifests:
            return ()
        try:
            document = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(document, dict):
            return ()
        packages: list[str] = []
        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            values = document.get(key)
            if isinstance(values, dict):
                packages.extend(name for name in values if isinstance(name, str))
        return tuple(packages)


class DependencyEnvironmentPreflight:
    """Validate that a deterministic dependency profile can be prepared before dispatch."""

    def __init__(
        self,
        *,
        cache_root: Path,
        python_image: str,
        node_image: str,
        proxy_url: str | None,
        python_index_url: str,
        node_registry_url: str,
        timeout_seconds: float = 10.0,
        build_timeout_seconds: float = 60.0,
        max_cache_bytes: int = 5 * 1024 * 1024 * 1024,
        docker_executable: str = "docker",
    ) -> None:
        self._cache_root = cache_root.resolve()
        self._python_image = python_image
        self._node_image = node_image
        self._proxy_url = proxy_url
        self._python_index_url = python_index_url.rstrip("/") + "/"
        self._node_registry_url = node_registry_url.rstrip("/") + "/"
        self._timeout_seconds = timeout_seconds
        self._build_timeout_seconds = build_timeout_seconds
        self._docker_executable = docker_executable
        self._inspector = DependencyManifestInspector()
        self._profile_resolver = ProjectVerificationProfileResolver()
        self._cache_builder = ProjectDependencyCacheBuilder(
            root=self._cache_root,
            python_image=python_image,
            node_image=node_image,
            build_timeout_seconds=build_timeout_seconds,
            proxy_url=proxy_url,
            python_index_url=python_index_url,
            node_registry_url=node_registry_url,
            max_cache_bytes=max_cache_bytes,
        )

    def check(
        self,
        workspace: Path,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> DependencyPreflightReport:
        root = workspace.resolve()
        facts = self._inspector.inspect(root)
        if facts.package_manager is DependencyPackageManager.MIXED:
            raise self._error(
                DependencyPreflightFailureCode.MIXED_PROJECT,
                facts,
                "当前版本不能为 Python 与 Node 混合依赖生成单一的确定性验证环境。",
            )
        try:
            profile = self._profile_resolver.resolve(root)
        except ProjectVerificationEnvironmentError as exc:
            raise self._error(
                DependencyPreflightFailureCode.UNSUPPORTED_PROFILE,
                facts,
                str(exc),
            ) from exc

        required_runtimes = self._required_runtimes(profile.kind, verification_commands)
        docker_version = self._check_docker(required_runtimes, facts)
        cache_state = self._check_cache(profile.digest, profile.kind, facts)
        registry_url: str | None = None
        if cache_state == "MISS" and facts.packages:
            registry_url = self._registry_url(facts.package_manager)
            registry_deadline = monotonic() + self._timeout_seconds
            for package in facts.packages[:_MAX_PACKAGE_PROBES]:
                remaining = registry_deadline - monotonic()
                if remaining <= 0:
                    raise self._error(
                        DependencyPreflightFailureCode.REGISTRY_UNREACHABLE,
                        facts,
                        f"访问登记源 {registry_url} 超过 {self._timeout_seconds:g} 秒预检预算。",
                    )
                self._check_registry_package(
                    registry_url,
                    package,
                    facts,
                    timeout_seconds=remaining,
                )
        if cache_state == "MISS":
            try:
                self._cache_builder.ensure(root, profile)
            except ProjectVerificationEnvironmentError as exc:
                raise self._error(
                    DependencyPreflightFailureCode.DEPENDENCY_INSTALL_FAILED,
                    facts,
                    (
                        f"使用 {facts.package_manager.value} 准备依赖缓存未完成，"
                        f"预检上限为 {self._build_timeout_seconds:g} 秒：{exc}"
                    ),
                ) from exc

        return DependencyPreflightReport(
            profile_kind=profile.kind,
            dependency_fingerprint=profile.digest,
            package_manager=facts.package_manager,
            manifest_paths=facts.manifest_paths,
            packages=facts.packages,
            cache_state=cache_state,
            docker_version=docker_version,
            registry_url=registry_url,
            proxy_configured=self._proxy_url is not None,
            required_runtimes=required_runtimes,
        )

    def status(self, workspace: Path) -> DependencyEnvironmentStatus:
        root = workspace.resolve()
        facts = self._inspector.inspect(root)
        profile = self._resolve_profile(root, facts)
        if profile.kind is ProjectVerificationKind.PYTHON_BASE:
            return DependencyEnvironmentStatus(
                dependency_fingerprint=profile.digest,
                profile_kind=profile.kind,
                package_manager=facts.package_manager,
                cache_state="NOT_REQUIRED",
                artifact_bytes=0,
                build_duration_ms=None,
                log_tail="Python 标准库验证环境不需要额外依赖缓存。",
            )
        artifact = self._cache_builder.artifact(profile)
        if artifact is None:
            failure_log = self._cache_builder.failure_log(profile)
            return DependencyEnvironmentStatus(
                dependency_fingerprint=profile.digest,
                profile_kind=profile.kind,
                package_manager=facts.package_manager,
                cache_state="MISS",
                artifact_bytes=0,
                build_duration_ms=None,
                log_tail=self._log_tail(failure_log),
            )
        metadata = json.loads(artifact.metadata_path.read_text(encoding="utf-8"))
        duration = metadata.get("build_duration_ms")
        return DependencyEnvironmentStatus(
            dependency_fingerprint=profile.digest,
            profile_kind=profile.kind,
            package_manager=facts.package_manager,
            cache_state="HIT",
            artifact_bytes=self._directory_size(artifact.root),
            build_duration_ms=duration if isinstance(duration, int) and duration >= 0 else None,
            log_tail=self._log_tail(artifact.log_path),
        )

    def rebuild(self, workspace: Path) -> DependencyEnvironmentStatus:
        root = workspace.resolve()
        facts = self._inspector.inspect(root)
        profile = self._resolve_profile(root, facts)
        if profile.kind is ProjectVerificationKind.PYTHON_BASE:
            return self.status(root)
        self._check_docker(self._required_runtimes(profile.kind, ()), facts)
        self._cache_builder.rebuild(root, profile)
        return self.status(root)

    def cleanup(self) -> DependencyCacheCleanup:
        return self._cache_builder.cleanup()

    def metrics(self) -> DependencyEnvironmentMetrics:
        values = self._cache_builder.metrics()
        attempts = values["hits"] + values["builds"] + values["failures"]
        return DependencyEnvironmentMetrics(
            cache_hits=values["hits"],
            builds=values["builds"],
            failures=values["failures"],
            hit_rate=(values["hits"] / attempts) if attempts else 0.0,
            average_build_duration_ms=(
                values["build_duration_ms_total"] // values["builds"] if values["builds"] else 0
            ),
            cache_bytes=self._directory_size(self._cache_root),
        )

    def _resolve_profile(self, root: Path, facts: _ManifestFacts):
        if facts.package_manager is DependencyPackageManager.MIXED:
            raise self._error(
                DependencyPreflightFailureCode.MIXED_PROJECT,
                facts,
                "当前版本不能为 Python 与 Node 混合依赖生成单一的确定性验证环境。",
            )
        try:
            return self._profile_resolver.resolve(root)
        except ProjectVerificationEnvironmentError as exc:
            raise self._error(
                DependencyPreflightFailureCode.UNSUPPORTED_PROFILE,
                facts,
                str(exc),
            ) from exc

    @staticmethod
    def _directory_size(path: Path) -> int:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())

    @staticmethod
    def _log_tail(path: Path | None) -> str:
        if path is None or not path.is_file():
            return "尚未生成依赖构建日志。"
        return path.read_text(encoding="utf-8", errors="replace")[-8_000:]

    def _check_docker(
        self,
        runtimes: tuple[VerificationRuntime, ...],
        facts: _ManifestFacts,
    ) -> str:
        try:
            version = subprocess.run(
                [self._docker_executable, "version", "--format", "{{.Server.Version}}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise self._error(
                DependencyPreflightFailureCode.DOCKER_UNAVAILABLE,
                facts,
                "Docker Desktop 不可访问。",
            ) from exc
        if version.returncode != 0 or not version.stdout.strip():
            raise self._error(
                DependencyPreflightFailureCode.DOCKER_UNAVAILABLE,
                facts,
                "Docker Desktop 未运行或当前进程没有访问权限。",
            )
        for runtime in runtimes:
            image = self._runtime_image(runtime)
            label = self._runtime_label(runtime)
            try:
                image_result = subprocess.run(
                    [self._docker_executable, "image", "inspect", image],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise self._error(
                    DependencyPreflightFailureCode.VERIFIER_IMAGE_UNAVAILABLE,
                    facts,
                    f"无法检查 {label} 可信验证镜像 {image}。",
                ) from exc
            if image_result.returncode != 0:
                raise self._error(
                    DependencyPreflightFailureCode.VERIFIER_IMAGE_UNAVAILABLE,
                    facts,
                    f"{label} 可信验证镜像 {image} 尚未构建。",
                )
            executable = "node" if runtime is VerificationRuntime.NODE else "python"
            try:
                capability = subprocess.run(
                    [
                        self._docker_executable,
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--entrypoint",
                        executable,
                        image,
                        "--version",
                    ],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise self._error(
                    DependencyPreflightFailureCode.VERIFIER_RUNTIME_UNAVAILABLE,
                    facts,
                    f"验证环境缺少 {label}：无法执行 {image} 中的 {executable} --version。",
                ) from exc
            if capability.returncode != 0 or not capability.stdout.strip():
                diagnostic = (capability.stderr or capability.stdout).strip()
                suffix = f" Docker 诊断：{diagnostic[:240]}" if diagnostic else ""
                raise self._error(
                    DependencyPreflightFailureCode.VERIFIER_RUNTIME_UNAVAILABLE,
                    facts,
                    f"验证环境缺少 {label}：镜像 {image} 无法运行 {executable}。{suffix}",
                )
        return version.stdout.strip()

    def _required_runtimes(
        self,
        profile_kind: ProjectVerificationKind,
        verification_commands: tuple[str, ...],
    ) -> tuple[VerificationRuntime, ...]:
        runtimes = {
            VerificationRuntime.NODE
            if profile_kind is ProjectVerificationKind.NODE_NPM_LOCK
            else VerificationRuntime.PYTHON
        }
        runtimes.update(
            verification_runtime_for_command(command) for command in verification_commands
        )
        return tuple(sorted(runtimes, key=lambda item: item.value))

    def _runtime_image(self, runtime: VerificationRuntime) -> str:
        return self._node_image if runtime is VerificationRuntime.NODE else self._python_image

    @staticmethod
    def _runtime_label(runtime: VerificationRuntime) -> str:
        return "Node.js" if runtime is VerificationRuntime.NODE else "Python"

    def _check_cache(
        self,
        digest: str,
        profile_kind: ProjectVerificationKind,
        facts: _ManifestFacts,
    ) -> str:
        if profile_kind is ProjectVerificationKind.PYTHON_BASE:
            return "NOT_REQUIRED"
        try:
            self._cache_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self._cache_root, delete=False) as handle:
                probe = Path(handle.name)
            probe.unlink(missing_ok=True)
        except OSError as exc:
            raise self._error(
                DependencyPreflightFailureCode.CACHE_UNAVAILABLE,
                facts,
                "依赖缓存目录不可创建或不可写入。",
            ) from exc
        return "HIT" if (self._cache_root / digest / ".complete").is_file() else "MISS"

    def _registry_url(self, package_manager: DependencyPackageManager) -> str:
        if package_manager is DependencyPackageManager.PYTHON:
            return self._python_index_url
        if package_manager is DependencyPackageManager.NODE:
            return self._node_registry_url
        raise AssertionError(f"registry lookup is unsupported for {package_manager.value}")

    def _check_registry_package(
        self,
        registry_url: str,
        package: str,
        facts: _ManifestFacts,
        *,
        timeout_seconds: float,
    ) -> None:
        encoded = quote(package, safe="@")
        target = urljoin(registry_url, f"{encoded}/")
        handlers = []
        if self._proxy_url is not None:
            handlers.append(ProxyHandler({"http": self._proxy_url, "https": self._proxy_url}))
        request = Request(
            target,
            method="HEAD",
            headers={"User-Agent": "DevFlow-dependency-preflight"},
        )
        try:
            with build_opener(*handlers).open(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", response.getcode())
        except HTTPError as exc:
            status = exc.code
        except (OSError, URLError) as exc:
            raise self._error(
                DependencyPreflightFailureCode.REGISTRY_UNREACHABLE,
                facts,
                f"无法通过已配置的网络访问 {target}。",
            ) from exc
        if not 200 <= status < 400:
            raise self._error(
                (
                    DependencyPreflightFailureCode.PACKAGE_UNAVAILABLE
                    if status == 404
                    else DependencyPreflightFailureCode.REGISTRY_UNREACHABLE
                ),
                facts,
                f"包 {package} 在登记源 {registry_url} 不可用（HTTP {status}）。",
            )

    @staticmethod
    def _error(
        code: DependencyPreflightFailureCode,
        facts: _ManifestFacts,
        reason: str,
    ) -> DependencyEnvironmentPreflightError:
        return DependencyEnvironmentPreflightError(
            code=code,
            package_manager=facts.package_manager,
            manifest_paths=facts.manifest_paths,
            packages=facts.packages,
            reason=reason,
        )
