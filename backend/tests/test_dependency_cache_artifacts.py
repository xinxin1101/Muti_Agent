from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.models import DockerSandboxPolicy, FailureType, VerificationBackend
from app.verification.project_profile import (
    ProjectAwareVerificationRunner,
    ProjectDependencyCacheBuilder,
    ProjectVerificationProfileResolver,
)
from app.verification.sandbox import DockerSandboxRunner, VerificationExecution


def _artifact_root(command: list[str]) -> Path:
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "--mount"]
    artifact_mount = next(item for item in mounts if "dst=/artifact" in item)
    source = next(
        item.removeprefix("src=") for item in artifact_mount.split(",") if item.startswith("src=")
    )
    return Path(source)


def _successful_dependency_container(
    calls: list[list[str]],
):
    def run(command, **_kwargs):
        recorded = list(command)
        calls.append(recorded)
        artifact = _artifact_root(recorded)
        command_text = " ".join(recorded)
        if "python" in recorded and "wheel" in recorded:
            (artifact / "wheels").mkdir(parents=True)
            (artifact / "wheels" / "requests-2.32.5-py3-none-any.whl").touch()
        elif "python" in recorded:
            (artifact / "python-site-packages").mkdir(parents=True)
            (artifact / "python-site-packages" / "requests.py").touch()
        elif "--offline" in command_text:
            (artifact / "node_modules").mkdir(parents=True)
            (artifact / "node_modules" / "left-pad.js").touch()
        else:
            (artifact / "npm-cache").mkdir(parents=True)
            (artifact / "npm-cache" / "cache-entry").touch()
        return subprocess.CompletedProcess(
            recorded,
            0,
            "downloaded through http://127.0.0.1:7897 token=super-secret",
            "",
        )

    return run


def test_python_artifact_uses_wheels_then_offline_install_and_reuses_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    profile = ProjectVerificationProfileResolver().resolve(workspace)
    cache_root = tmp_path / "cache"
    builder = ProjectDependencyCacheBuilder(
        root=cache_root,
        python_image="devflow-verifier:py311",
        node_image="devflow-verifier:node24",
        proxy_url="http://127.0.0.1:7897",
        python_index_url="https://pypi.example/simple/",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _successful_dependency_container(calls))

    first = builder.ensure(workspace, profile)
    second = builder.ensure(workspace, profile)

    assert first is not None
    assert second is not None
    assert first.source == cache_root / profile.digest / "python-site-packages"
    assert len(calls) == 2
    assert calls[0][calls[0].index("--network") + 1] == "bridge"
    assert calls[1][calls[1].index("--network") + 1] == "none"
    assert "--no-index" in calls[1]
    artifact = cache_root / profile.digest
    metadata = json.loads((artifact / "artifact.json").read_text(encoding="utf-8"))
    assert metadata["dependency_fingerprint"] == profile.digest
    assert metadata["dependency_manifest"]["locked_packages"] == ["requests==2.32.5"]
    assert metadata["verification_network"] == "none"
    assert "127.0.0.1:7897" not in (artifact / "build.log").read_text(encoding="utf-8")
    assert "super-secret" not in (artifact / "build.log").read_text(encoding="utf-8")
    assert builder.metrics()["builds"] == 1
    assert builder.metrics()["hits"] == 1
    cleanup = builder.cleanup(max_cache_bytes=0)
    assert cleanup.removed_fingerprints == (profile.digest,)
    assert cleanup.reclaimed_bytes > 0


def test_node_artifact_persists_npm_cache_and_offline_node_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "package.json").write_text(
        '{"dependencies":{"left-pad":"1.3.0","private":"https://secret@example.test/pkg"}}',
        encoding="utf-8",
    )
    (workspace / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    profile = ProjectVerificationProfileResolver().resolve(workspace)
    cache_root = tmp_path / "cache"
    builder = ProjectDependencyCacheBuilder(
        root=cache_root,
        python_image="devflow-verifier:py311",
        node_image="devflow-verifier:node24",
        proxy_url="http://127.0.0.1:7897",
        node_registry_url="https://registry.example/",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _successful_dependency_container(calls))

    mount = builder.ensure(workspace, profile)

    assert mount is not None
    assert mount.source == cache_root / profile.digest / "node_modules"
    assert len(calls) == 2
    assert calls[0][calls[0].index("--network") + 1] == "bridge"
    assert calls[1][calls[1].index("--network") + 1] == "none"
    artifact = cache_root / profile.digest
    metadata_text = (artifact / "artifact.json").read_text(encoding="utf-8")
    assert "npm-cache+node_modules" in metadata_text
    assert "secret" not in metadata_text
    assert "127.0.0.1:7897" not in (artifact / "build.log").read_text(encoding="utf-8")


def test_bare_node_command_uses_node_verifier_image_without_package_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    runner = ProjectAwareVerificationRunner(
        base_policy=DockerSandboxPolicy(image="devflow-verifier:py311"),
        node_image="devflow-verifier:node24",
        cache_root=tmp_path / "cache",
    )
    images: list[str] = []

    def run(self, _argv, **_kwargs) -> VerificationExecution:
        images.append(self.policy.image)
        return VerificationExecution(
            exit_code=0,
            stdout="ok\n",
            stderr="",
            duration_ms=1,
            backend=VerificationBackend.DOCKER,
        )

    monkeypatch.setattr(DockerSandboxRunner, "run", run)

    result = runner.run(["node", "test/test_game.js"], workspace=workspace, timeout_seconds=5)

    assert result.exit_code == 0
    assert images == ["devflow-verifier:node24"]


def test_missing_node_runtime_is_an_environment_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    runner = ProjectAwareVerificationRunner(
        base_policy=DockerSandboxPolicy(image="devflow-verifier:py311"),
        node_image="devflow-verifier:node24",
        cache_root=tmp_path / "cache",
    )

    def run(_self, _argv, **_kwargs) -> VerificationExecution:
        return VerificationExecution(
            exit_code=127,
            stdout="",
            stderr='exec: "node": executable file not found in $PATH',
            duration_ms=1,
            backend=VerificationBackend.DOCKER,
            failure_type=FailureType.TOOL_FAILURE,
        )

    monkeypatch.setattr(DockerSandboxRunner, "run", run)

    result = runner.run(["node", "test/test_game.js"], workspace=workspace, timeout_seconds=5)

    assert result.failure_type is FailureType.VERIFICATION_ENV_UNAVAILABLE
    assert "验证环境缺少 Node.js" in result.stderr
