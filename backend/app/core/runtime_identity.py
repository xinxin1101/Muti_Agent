"""Runtime source identity used to prevent mixed API/Worker deployments."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_APPLICATION_ROOT = _REPOSITORY_ROOT / "backend" / "app"
_FINGERPRINT_ENV = "DEVFLOW_RUNTIME_FINGERPRINT"


def current_runtime_fingerprint() -> str:
    """Return a deterministic digest of the Python application source tree."""

    digest = hashlib.sha256()
    for source_file in sorted(_APPLICATION_ROOT.rglob("*.py")):
        relative_path = source_file.relative_to(_REPOSITORY_ROOT).as_posix().encode("utf-8")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(source_file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def assert_runtime_fingerprint() -> str:
    """Reject a process launched against source different from its launcher identity.

    Direct development/test invocations remain supported: the assertion is enforced only
    when a launcher explicitly provides ``DEVFLOW_RUNTIME_FINGERPRINT``.
    """

    actual = current_runtime_fingerprint()
    expected = os.environ.get(_FINGERPRINT_ENV, "").strip()
    if expected and expected != actual:
        raise RuntimeError(
            "DevFlow runtime source changed after this process was launched. "
            "Restart both API and Worker using scripts/start-local.ps1."
        )
    return actual
