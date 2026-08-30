from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.verification.dependency_preflight import (
    DependencyEnvironmentPreflight,
    DependencyEnvironmentPreflightError,
    DependencyPackageManager,
    DependencyPreflightFailureCode,
)
from app.verification.project_profile import (
    ProjectVerificationEnvironmentError,
    ProjectVerificationKind,
)


def _preflight(tmp_path: Path) -> DependencyEnvironmentPreflight:
    return DependencyEnvironmentPreflight(
        cache_root=tmp_path / "cache",
        python_image="devflow-verifier:py311",
        node_image="devflow-verifier:node24",
        proxy_url="http://127.0.0.1:7897",
        python_index_url="https://pypi.example/simple/",
        node_registry_url="https://npm.example/",
    )


def _make_environment_ready(
    monkeypatch: pytest.MonkeyPatch,
    preflight: DependencyEnvironmentPreflight,
) -> None:
    monkeypatch.setattr(preflight, "_check_docker", lambda *_args: "28.3.2")
    monkeypatch.setattr(preflight, "_check_registry_package", lambda *_args, **_kwargs: None)

    def build_cache(_workspace: Path, profile) -> None:
        target = preflight._cache_root / profile.digest
        target.mkdir(parents=True, exist_ok=True)
        (target / ".complete").write_text(profile.kind.value, encoding="utf-8")

    monkeypatch.setattr(preflight._cache_builder, "ensure", build_cache)


def test_preflight_identifies_pinned_python_dependencies_and_stable_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text(
        "requests==2.32.5\npytest==9.1.1\n",
        encoding="utf-8",
    )
    preflight = _preflight(tmp_path)
    _make_environment_ready(monkeypatch, preflight)

    first = preflight.check(root)
    second = preflight.check(root)

    assert first.profile_kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
    assert first.package_manager is DependencyPackageManager.PYTHON
    assert first.manifest_paths == ("requirements.txt",)
    assert first.packages == ("pytest", "requests")
    assert first.cache_state == "MISS"
    assert second.cache_state == "HIT"
    assert first.dependency_fingerprint == second.dependency_fingerprint
    assert first.proxy_configured is True


def test_preflight_supports_pinned_requirements_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements-dev.lock").write_text("rich==14.2.0\n", encoding="utf-8")
    preflight = _preflight(tmp_path)
    _make_environment_ready(monkeypatch, preflight)

    result = preflight.check(root)

    assert result.profile_kind is ProjectVerificationKind.PYTHON_PINNED_REQUIREMENTS
    assert result.manifest_paths == ("requirements-dev.lock",)
    assert result.packages == ("rich",)


def test_preflight_rejects_unlocked_pyproject_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    preflight = _preflight(tmp_path)
    _make_environment_ready(monkeypatch, preflight)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.UNSUPPORTED_PROFILE
    assert "pyproject.toml" in captured.value.public_detail


def test_preflight_reports_the_specific_package_when_registry_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("missing-package==1.0.0\n", encoding="utf-8")
    preflight = _preflight(tmp_path)
    monkeypatch.setattr(preflight, "_check_docker", lambda *_args: "28.3.2")

    def fail(_registry: str, package: str, facts, **_kwargs) -> None:
        raise preflight._error(
            DependencyPreflightFailureCode.PACKAGE_UNAVAILABLE,
            facts,
            f"包 {package} 不存在。",
        )

    monkeypatch.setattr(preflight, "_check_registry_package", fail)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.PACKAGE_UNAVAILABLE
    assert "missing-package" in captured.value.public_detail


def test_preflight_reports_proxy_disconnect_before_dependency_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    preflight = _preflight(tmp_path)
    monkeypatch.setattr(preflight, "_check_docker", lambda *_args: "28.3.2")

    def disconnected(_registry: str, _package: str, facts, **_kwargs) -> None:
        raise preflight._error(
            DependencyPreflightFailureCode.REGISTRY_UNREACHABLE,
            facts,
            "Clash 代理连接已断开。",
        )

    def should_not_build(*_args, **_kwargs) -> None:
        raise AssertionError("代理不可用时不得进入依赖构建或开发阶段")

    monkeypatch.setattr(preflight, "_check_registry_package", disconnected)
    monkeypatch.setattr(preflight._cache_builder, "ensure", should_not_build)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.REGISTRY_UNREACHABLE
    assert "Clash" in captured.value.public_detail


def test_preflight_rejects_mixed_dependency_manifests_before_docker_check(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("requests==2.32.5\n", encoding="utf-8")
    (root / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
    preflight = _preflight(tmp_path)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.MIXED_PROJECT


def test_preflight_rejects_orphan_node_lock_before_docker_check(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    preflight = _preflight(tmp_path)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.UNSUPPORTED_PROFILE
    assert "package-lock.json" in captured.value.public_detail


def test_preflight_reports_install_failure_before_any_agent_is_dispatched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "requirements.txt").write_text("pygame==2.6.1\n", encoding="utf-8")
    preflight = _preflight(tmp_path)
    monkeypatch.setattr(preflight, "_check_docker", lambda *_args: "28.3.2")
    monkeypatch.setattr(preflight, "_check_registry_package", lambda *_args, **_kwargs: None)

    def fail_cache(_workspace: Path, _profile) -> None:
        raise ProjectVerificationEnvironmentError("dependency cache build failed")

    monkeypatch.setattr(preflight._cache_builder, "ensure", fail_cache)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root)

    assert captured.value.code is DependencyPreflightFailureCode.DEPENDENCY_INSTALL_FAILED
    assert "pygame" in captured.value.public_detail


def test_preflight_checks_node_runtime_for_a_bare_javascript_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    preflight = _preflight(tmp_path)
    calls: list[list[str]] = []

    def docker(command, **_kwargs):
        recorded = list(command)
        calls.append(recorded)
        if recorded[1:3] == ["version", "--format"]:
            return subprocess.CompletedProcess(recorded, 0, "28.3.2\n", "")
        if recorded[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(recorded, 0, "[]\n", "")
        if recorded[1:3] == ["run", "--rm"]:
            executable = recorded[recorded.index("--entrypoint") + 1]
            return subprocess.CompletedProcess(recorded, 0, f"{executable} v1\n", "")
        raise AssertionError(recorded)

    monkeypatch.setattr(subprocess, "run", docker)

    report = preflight.check(root, verification_commands=("node test/test_game.js",))

    assert {runtime.value for runtime in report.required_runtimes} == {"PYTHON", "NODE"}
    assert any(
        "--entrypoint" in call and call[call.index("--entrypoint") + 1] == "node" for call in calls
    )


def test_preflight_reports_missing_node_runtime_before_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    preflight = _preflight(tmp_path)

    def docker(command, **_kwargs):
        recorded = list(command)
        if recorded[1:3] == ["version", "--format"]:
            return subprocess.CompletedProcess(recorded, 0, "28.3.2\n", "")
        if recorded[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(recorded, 0, "[]\n", "")
        if recorded[1:3] == ["run", "--rm"]:
            executable = recorded[recorded.index("--entrypoint") + 1]
            if executable == "node":
                return subprocess.CompletedProcess(recorded, 127, "", "node: not found")
            return subprocess.CompletedProcess(recorded, 0, "Python 3.11\n", "")
        raise AssertionError(recorded)

    monkeypatch.setattr(subprocess, "run", docker)

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        preflight.check(root, verification_commands=("node test/test_game.js",))

    assert captured.value.code is DependencyPreflightFailureCode.VERIFIER_RUNTIME_UNAVAILABLE
    assert "验证环境缺少 Node.js" in captured.value.public_detail
