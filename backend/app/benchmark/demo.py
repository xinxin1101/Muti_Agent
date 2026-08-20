from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from app.benchmark.io import canonical_sha256
from app.benchmark.models import BenchmarkDemoManifest, BenchmarkDemoResult


def load_demo_manifest(path: Path) -> tuple[BenchmarkDemoManifest, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = BenchmarkDemoManifest.model_validate(payload)
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
        raise ValueError("control-plane demo requires a Git checkout with a valid HEAD")
    commit = completed.stdout.strip().lower()
    if len(commit) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("control-plane demo Git HEAD is not a canonical object id")
    return commit


def run_control_plane_demo(
    manifest_path: Path,
    *,
    repository_root: Path,
    timeout_seconds: float,
) -> BenchmarkDemoResult:
    """Run versioned deterministic runtime regressions without a privileged demo runtime."""

    if timeout_seconds < 1 or timeout_seconds > 900:
        raise ValueError("control-plane demo timeout must be between 1 and 900 seconds")
    manifest, manifest_sha256 = load_demo_manifest(manifest_path)
    root = repository_root.resolve()
    if not (root / "tests").is_dir():
        raise ValueError(
            "control-plane demo repository_root must contain the backend tests directory"
        )

    nodeids = [item.pytest_nodeid for item in manifest.scenarios]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *nodeids],
        cwd=root,
        check=False,
        timeout=timeout_seconds,
    )
    return BenchmarkDemoResult(
        manifest_id=manifest.manifest_id,
        manifest_version=manifest.manifest_version,
        manifest_sha256=manifest_sha256,
        runtime_commit=_runtime_commit(root),
        scenario_count=len(nodeids),
        exit_code=completed.returncode,
        passed=completed.returncode == 0,
    )


__all__ = [
    "load_demo_manifest",
    "run_control_plane_demo",
]
