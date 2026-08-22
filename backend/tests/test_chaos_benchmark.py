from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.benchmark.chaos import (
    ChaosFaultDomain,
    ChaosInvariant,
    ChaosRecoveryManifest,
    load_chaos_manifest,
    run_chaos_recovery_benchmark,
)


def _manifest_path() -> Path:
    return Path(__file__).parents[2] / "benchmarks" / "v1_1" / "chaos-recovery.json"


def _payload() -> dict:
    return json.loads(_manifest_path().read_text(encoding="utf-8"))


def test_repository_chaos_manifest_covers_frozen_matrix() -> None:
    manifest, digest = load_chaos_manifest(_manifest_path())

    assert manifest.suite_id == "devflow-v1-1-chaos-recovery"
    assert len(digest) == 64
    assert {item.fault_domain for item in manifest.scenarios} == set(ChaosFaultDomain)
    assert set(manifest.required_invariants) == set(ChaosInvariant)
    assert len(manifest.scenarios) == 8


def test_chaos_manifest_rejects_duplicate_scenario_identity() -> None:
    payload = _payload()
    payload["scenarios"][1]["scenario_id"] = payload["scenarios"][0]["scenario_id"]

    with pytest.raises(ValidationError, match="scenario_id values must be unique"):
        ChaosRecoveryManifest.model_validate(payload)


def test_chaos_manifest_rejects_duplicate_pytest_nodeid() -> None:
    payload = _payload()
    payload["scenarios"][1]["pytest_nodeid"] = payload["scenarios"][0]["pytest_nodeid"]

    with pytest.raises(ValidationError, match="pytest nodeids must be unique"):
        ChaosRecoveryManifest.model_validate(payload)


def test_chaos_manifest_rejects_missing_fault_domain() -> None:
    payload = _payload()
    payload["scenarios"][0]["fault_domain"] = "DAG"

    with pytest.raises(ValidationError, match="missing required fault domains"):
        ChaosRecoveryManifest.model_validate(payload)


def test_chaos_manifest_rejects_missing_required_invariant() -> None:
    payload = _payload()
    payload["required_invariants"] = payload["required_invariants"][:-1]

    with pytest.raises(ValidationError):
        ChaosRecoveryManifest.model_validate(payload)


def test_chaos_manifest_rejects_unsafe_pytest_selector() -> None:
    payload = _payload()
    payload["scenarios"][0]["pytest_nodeid"] = "../../tests/test_escape.py::test_escape"

    with pytest.raises(ValidationError):
        ChaosRecoveryManifest.model_validate(payload)


def test_chaos_runner_rejects_invalid_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout must be between"):
        run_chaos_recovery_benchmark(
            _manifest_path(),
            repository_root=tmp_path,
            timeout_seconds=0,
        )


def test_chaos_runner_requires_backend_test_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must contain backend tests"):
        run_chaos_recovery_benchmark(
            _manifest_path(),
            repository_root=tmp_path,
            timeout_seconds=30,
        )
