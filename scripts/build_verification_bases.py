from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _build(dockerfile: str, tag: str) -> None:
    result = subprocess.run(
        ["docker", "build", "-f", f"docker/{dockerfile}", "-t", tag, "."],
        cwd=BACKEND,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"failed to build trusted verification base: {tag}")


def main() -> int:
    _build("verification.Dockerfile", "devflow-verifier:py311")
    _build("verification-node.Dockerfile", "devflow-verifier:node24")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
