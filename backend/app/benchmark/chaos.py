from __future__ import annotations

import json
import subprocess
import sys
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.benchmark.io import canonical_sha256
from app.benchmark.models import BenchmarkModel


class ChaosFaultDomain(StrEnum):
    LEASE = "LEASE"
    FENCING = "FENCING"
    GIT = "GIT"
    PROCESS_RESTART = "PROCESS_RESTART"
    CONCURRENCY = "CONCURRENCY"
    BROKER = "BROKER"
    HUMAN_GATE = "HUMAN_GATE"
    DAG = "DAG"
    OPERATOR = "OPERATOR"
    REPAIR = "REPAIR"


class ChaosInvariant(StrEnum):
    AT_MOST_ONE_MUTATION = "AT_MOST_ONE_MUTATION"
    STALE_GENERATION_FENCED = "STALE_GENERATION_FENCED"
    UNKNOWN_STATE_NOT_GUESSED = "UNKNOWN_STATE_NOT_GUESSED"
    FAILURE_NOT_SUCCESS = "FAILURE_NOT_SUCCESS"
    COMPLETED_WORK_NOT_RERUN = "COMPLETED_WORK_NOT_RERUN"
    HUMAN_DECISION_DURABLE = "HUMAN_DECISION_DURABLE"
    OBSERVABILITY_NOT_AUTHORITY = "OBSERVABILITY_NOT_AUTHORITY"


_REQUIRED_FAULT_DOMAINS = frozenset(ChaosFaultDomain)
_REQUIRED_INVARIANTS = frozenset(ChaosInvariant)


class ChaosScenario(BenchmarkModel):
    scenario_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^C[0-9]{2}_[A-Z0-9_]+$",
    )
    fault_domain: ChaosFaultDomain
    title: str = Field(min_length=1, max_length=1000)
    pytest_nodeid: str = Field(
        pattern=r"^tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+$",
        max_length=256,
    )
    invariants: tuple[ChaosInvariant, ...] = Field(min_length=1, max_length=7)

    @field_validator("invariants")
    @classmethod
    def validate_unique_invariants(
        cls,
        values: tuple[ChaosInvariant, ...],
    ) -> tuple[ChaosInvariant, ...]:
        if len(values) != len(set(values)):
            raise ValueError("chaos scenario invariants must be unique")
        return values


class ChaosRecoveryManifest(BenchmarkModel):
    schema_version: Literal[1] = 1
    suite_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    suite_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    description: str = Field(min_length=1, max_length=4000)
    required_invariants: tuple[ChaosInvariant, ...] = Field(min_length=7, max_length=7)
    scenarios: tuple[ChaosScenario, ...] = Field(min_length=10, max_length=32)

    @field_validator("required_invariants")
    @classmethod
    def validate_required_invariants(
        cls,
        values: tuple[ChaosInvariant, ...],
    ) -> tuple[ChaosInvariant, ...]:
        if len(values) != len(set(values)):
            raise ValueError("chaos required_invariants must be unique")
        if set(values) != _REQUIRED_INVARIANTS:
            raise ValueError("chaos manifest must require every frozen V1.1 invariant")
        return values

    @model_validator(mode="after")
    def validate_matrix(self) -> ChaosRecoveryManifest:
        scenario_ids = [item.scenario_id for item in self.scenarios]
        nodeids = [item.pytest_nodeid for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("chaos scenario_id values must be unique")
        if len(nodeids) != len(set(nodeids)):
            raise ValueError("chaos pytest nodeids must be unique")

        fault_domains = {item.fault_domain for item in self.scenarios}
        if not _REQUIRED_FAULT_DOMAINS.issubset(fault_domains):
            missing = sorted(item.value for item in _REQUIRED_FAULT_DOMAINS - fault_domains)
            raise ValueError(f"chaos manifest is missing required fault domains: {missing}")

        covered_invariants = {
            invariant
            for scenario in self.scenarios
            for invariant in scenario.invariants
        }
        if not _REQUIRED_INVARIANTS.issubset(covered_invariants):
            missing = sorted(item.value for item in _REQUIRED_INVARIANTS - covered_invariants)
            raise ValueError(f"chaos manifest is missing invariant coverage: {missing}")
        return self


class ChaosRecoveryResult(BenchmarkModel):
    suite_id: str
    suite_version: str
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    scenario_count: int = Field(ge=10)
    invariant_count: int = Field(ge=7)
    exit_code: int = Field(ge=0)
    passed: bool


def load_chaos_manifest(path: Path) -> tuple[ChaosRecoveryManifest, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = ChaosRecoveryManifest.model_validate(payload)
    return manifest, canonical_sha256(manifest)


def _runtime_commit(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise ValueError("chaos benchmark requires a Git checkout with a valid HEAD")
    commit = completed.stdout.strip().lower()
    if len(commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("chaos benchmark Git HEAD is not a canonical object id")
    return commit


def run_chaos_recovery_benchmark(
    manifest_path: Path,
    *,
    repository_root: Path,
    timeout_seconds: float,
) -> ChaosRecoveryResult:
    """Execute the frozen chaos matrix without introducing test-only runtime authority."""

    if timeout_seconds < 1 or timeout_seconds > 1800:
        raise ValueError("chaos benchmark timeout must be between 1 and 1800 seconds")
    manifest, manifest_sha256 = load_chaos_manifest(manifest_path)
    root = repository_root.resolve()
    if not (root / "tests").is_dir():
        raise ValueError("chaos benchmark repository_root must contain backend tests")

    nodeids = [item.pytest_nodeid for item in manifest.scenarios]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *nodeids],
        cwd=root,
        check=False,
        timeout=timeout_seconds,
    )
    return ChaosRecoveryResult(
        suite_id=manifest.suite_id,
        suite_version=manifest.suite_version,
        suite_sha256=manifest_sha256,
        runtime_commit=_runtime_commit(root),
        scenario_count=len(nodeids),
        invariant_count=len(manifest.required_invariants),
        exit_code=completed.returncode,
        passed=completed.returncode == 0,
    )


__all__ = [
    "ChaosFaultDomain",
    "ChaosInvariant",
    "ChaosRecoveryManifest",
    "ChaosRecoveryResult",
    "ChaosScenario",
    "load_chaos_manifest",
    "run_chaos_recovery_benchmark",
]
