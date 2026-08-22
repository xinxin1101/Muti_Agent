from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend"
API_HEALTH_URL = "http://127.0.0.1:8000/healthz"
FRONTEND_URL = "http://127.0.0.1:5173/"
STARTUP_TIMEOUT_SECONDS = 45.0


class SmokeFailure(RuntimeError):
    pass


def _wait_for_url(
    url: str,
    *,
    process: subprocess.Popen[bytes],
    log_path: Path,
    expected_json: dict[str, str] | None = None,
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        if process.poll() is not None:
            log = log_path.read_text(encoding="utf-8", errors="replace")
            raise SmokeFailure(
                f"process exited before {url} became ready (code={process.returncode})\n{log}"
            )

        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                payload = response.read()
                if response.status != 200:
                    raise SmokeFailure(f"{url} returned HTTP {response.status}")
                if expected_json is not None:
                    decoded = json.loads(payload.decode("utf-8"))
                    if decoded != expected_json:
                        raise SmokeFailure(
                            f"{url} returned unexpected JSON: {decoded!r}"
                        )
                return
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.5)

    log = log_path.read_text(encoding="utf-8", errors="replace")
    raise SmokeFailure(f"timed out waiting for {url}: {last_error}\n{log}")


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    env_file = REPOSITORY_ROOT / ".env"
    if not env_file.is_file():
        raise SmokeFailure(
            "repository-root .env is required for release smoke; copy .env.example first"
        )

    dramatiq = shutil.which("dramatiq")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if dramatiq is None:
        raise SmokeFailure("dramatiq executable is not available")
    if npm is None:
        raise SmokeFailure("npm executable is not available")

    # The smoke intentionally removes configuration variables that would mask the
    # repository-root `.env` behavior. Child processes must recover configuration
    # from that file while running from backend/ and frontend/ working directories.
    child_env = os.environ.copy()
    for name in (
        "DEVFLOW_DATABASE_URL",
        "DEVFLOW_REDIS_URL",
        "SILICONFLOW_API_KEY",
        "DEVFLOW_GITHUB_TOKEN",
    ):
        child_env.pop(name, None)

    api: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    frontend: subprocess.Popen[bytes] | None = None

    with tempfile.TemporaryDirectory(prefix="devflow-release-smoke-") as temp_dir:
        log_dir = Path(temp_dir)
        api_log_path = log_dir / "api.log"
        worker_log_path = log_dir / "worker.log"
        frontend_log_path = log_dir / "frontend.log"

        try:
            with (
                api_log_path.open("wb") as api_log,
                worker_log_path.open("wb") as worker_log,
                frontend_log_path.open("wb") as frontend_log,
            ):
                api = subprocess.Popen(
                    [sys.executable, "-m", "app.api.main"],
                    cwd=BACKEND_ROOT,
                    env=child_env,
                    stdout=api_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for_url(
                    API_HEALTH_URL,
                    process=api,
                    log_path=api_log_path,
                    expected_json={"status": "ok"},
                )

                worker = subprocess.Popen(
                    [dramatiq, "app.workers.tasks"],
                    cwd=BACKEND_ROOT,
                    env=child_env,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(2.0)
                if worker.poll() is not None:
                    raise SmokeFailure(
                        "Dramatiq worker exited during startup\n"
                        + worker_log_path.read_text(encoding="utf-8", errors="replace")
                    )

                frontend = subprocess.Popen(
                    [
                        npm,
                        "run",
                        "dev",
                        "--",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "5173",
                        "--strictPort",
                    ],
                    cwd=FRONTEND_ROOT,
                    env=child_env,
                    stdout=frontend_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for_url(
                    FRONTEND_URL,
                    process=frontend,
                    log_path=frontend_log_path,
                )
        finally:
            _stop(frontend)
            _stop(worker)
            _stop(api)

    print("release smoke: API + worker + frontend startup PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
