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
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
API_BASE_URL = "http://127.0.0.1:8000"
PROVIDER_BASE_URL = "http://127.0.0.1:18080"
STARTUP_TIMEOUT_SECONDS = 45.0
RUN_TIMEOUT_SECONDS = 180.0

DEFAULT_REPOSITORY_URL = "https://github.com/xinxin1101/Muti_Agent.git"
DEFAULT_REPOSITORY_BRANCH = "main"
TASK_FILES = {
    "distributed-e2e-a": "distributed_e2e_a.txt",
    "distributed-e2e-b": "distributed_e2e_b.txt",
}
TASK_ORDER = tuple(TASK_FILES)


class DistributedE2EFailure(RuntimeError):
    pass


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if not 200 <= response.status < 300:
                raise DistributedE2EFailure(f"{method} {url} returned HTTP {response.status}")
            decoded = json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DistributedE2EFailure(
            f"{method} {url} returned HTTP {exc.code}: {detail}"
        ) from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise DistributedE2EFailure(f"{method} {url} failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise DistributedE2EFailure(f"{method} {url} did not return a JSON object")
    return decoded


def _wait_for_json(
    url: str,
    *,
    process: subprocess.Popen[bytes],
    log_path: Path,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log = log_path.read_text(encoding="utf-8", errors="replace")
            raise DistributedE2EFailure(
                f"process exited before {url} became ready (code={process.returncode})\n{log}"
            )
        try:
            payload = _request_json("GET", url, timeout=2.0)
            if expected is not None and payload != expected:
                raise DistributedE2EFailure(
                    f"{url} returned unexpected JSON: {payload!r}; expected {expected!r}"
                )
            return payload
        except DistributedE2EFailure as exc:
            last_error = exc
            time.sleep(0.5)
    log = log_path.read_text(encoding="utf-8", errors="replace")
    raise DistributedE2EFailure(f"timed out waiting for {url}: {last_error}\n{log}")


def _wait_for_terminal_run(run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _request_json("GET", f"{API_BASE_URL}/api/v1/runs/{run_id}")
        status = last.get("status")
        if status in {"SUCCEEDED", "FAILED"}:
            return last
        time.sleep(0.5)
    raise DistributedE2EFailure(
        f"run {run_id} did not become terminal within {RUN_TIMEOUT_SECONDS}s; last={last!r}"
    )


def _provider_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            payload = json.loads(raw)
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _diagnostics(log_paths: dict[str, Path]) -> str:
    sections: list[str] = []
    for name, path in log_paths.items():
        content = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        sections.append(f"\n===== {name} =====\n{content[-12000:]}")
    return "".join(sections)


def _run_state_diagnostics(run_id: str) -> str:
    state: dict[str, Any] = {}
    for label, url in (
        ("run", f"{API_BASE_URL}/api/v1/runs/{run_id}"),
        ("dag", f"{API_BASE_URL}/api/v1/runs/{run_id}/dag"),
        ("metrics", f"{API_BASE_URL}/api/v1/runs/{run_id}/metrics"),
    ):
        try:
            state[label] = _request_json("GET", url)
        except DistributedE2EFailure as exc:
            state[label] = {"diagnostic_error": str(exc)}
    for task_id in TASK_ORDER:
        try:
            state[f"task:{task_id}"] = _request_json(
                "GET",
                f"{API_BASE_URL}/api/v1/runs/{run_id}/tasks/{task_id}",
            )
        except DistributedE2EFailure as exc:
            state[f"task:{task_id}"] = {"diagnostic_error": str(exc)}
    return "\n===== authoritative run state =====\n" + json.dumps(
        state,
        sort_keys=True,
        indent=2,
    )


def _assert_authoritative_result(run_id: str) -> None:
    dag = _request_json("GET", f"{API_BASE_URL}/api/v1/runs/{run_id}/dag")
    if tuple(dag.get("topological_order") or ()) != TASK_ORDER:
        raise DistributedE2EFailure(f"unexpected persisted DAG order: {dag!r}")
    edges = dag.get("edges") or []
    edge_pairs = {
        (item.get("source_task_id"), item.get("target_task_id"))
        for item in edges
        if isinstance(item, dict)
    }
    if edge_pairs != {("distributed-e2e-a", "distributed-e2e-b")}:
        raise DistributedE2EFailure(f"unexpected persisted DAG edges: {dag!r}")

    metrics = _request_json("GET", f"{API_BASE_URL}/api/v1/runs/{run_id}/metrics")
    if metrics.get("status") != "SUCCEEDED":
        raise DistributedE2EFailure(f"metrics did not project SUCCEEDED: {metrics!r}")
    evidence = metrics.get("evidence") or {}
    required_counts = {
        "verification_attempts": len(TASK_ORDER),
        "review_decisions": len(TASK_ORDER),
        "worker_executions": len(TASK_ORDER),
        "merge_queue_snapshots": len(TASK_ORDER),
    }
    for key, minimum in required_counts.items():
        if int(evidence.get(key, 0)) < minimum:
            raise DistributedE2EFailure(
                f"authoritative evidence count {key} was below {minimum}: {evidence!r}"
            )
    runtime_events = metrics.get("runtime_events") or {}
    if int(runtime_events.get("lease_acquisitions", 0)) < len(TASK_ORDER):
        raise DistributedE2EFailure(
            f"durable worker lease acquisitions were incomplete: {runtime_events!r}"
        )

    required_kinds = {
        "DEVELOPER_RUN",
        "VERIFICATION_RESULT",
        "REVIEW_DECISION",
        "WORKER_EXECUTION",
    }
    for task_id, expected_file in TASK_FILES.items():
        task = _request_json(
            "GET",
            f"{API_BASE_URL}/api/v1/runs/{run_id}/tasks/{task_id}",
        )
        kinds = {
            item.get("kind")
            for item in task.get("evidence", [])
            if isinstance(item, dict)
        }
        missing = sorted(required_kinds - kinds)
        if missing:
            raise DistributedE2EFailure(
                f"task {task_id} is missing authoritative evidence kinds {missing}: "
                f"{sorted(kinds)}"
            )

        integration_diff = _request_json(
            "GET",
            f"{API_BASE_URL}/api/v1/runs/{run_id}/tasks/{task_id}/diff?kind=INTEGRATION",
        )
        files = integration_diff.get("files") or []
        paths = {item.get("path") for item in files if isinstance(item, dict)}
        if expected_file not in paths:
            raise DistributedE2EFailure(
                f"integration diff for {task_id} omitted {expected_file}: {paths!r}"
            )
        if integration_diff.get("evidence_basis") != "MERGE_QUEUE_SNAPSHOT":
            raise DistributedE2EFailure(
                f"integration diff for {task_id} was not bound to MERGE_QUEUE_SNAPSHOT evidence"
            )


def _assert_provider_transport(path: Path) -> None:
    records = _provider_records(path)
    if not any(record.get("kind") == "models" for record in records):
        raise DistributedE2EFailure("provider /v1/models readiness path was not exercised")
    chat = [record for record in records if record.get("kind") == "chat.completions"]
    agents = [str(record.get("agent")) for record in chat]
    for required in ("planner", "developer", "reviewer"):
        if required not in agents:
            raise DistributedE2EFailure(
                f"OpenAI-compatible provider did not observe {required}: {agents!r}"
            )
    if agents.count("developer") < 2 * len(TASK_ORDER):
        raise DistributedE2EFailure(
            f"Developer tool round trips did not cross HTTP for both tasks: {agents!r}"
        )
    if agents.count("reviewer") < len(TASK_ORDER):
        raise DistributedE2EFailure(
            f"Reviewer did not cross HTTP for both tasks: {agents!r}"
        )
    if "repair" in agents:
        raise DistributedE2EFailure(
            "repair was unexpectedly invoked by the deterministic V2.5 happy path"
        )


def main() -> int:
    dramatiq = shutil.which("dramatiq")
    if dramatiq is None:
        raise DistributedE2EFailure("dramatiq executable is not available")

    repository_url = os.environ.get("DEVFLOW_E2E_REPOSITORY_URL", DEFAULT_REPOSITORY_URL)
    repository_branch = os.environ.get(
        "DEVFLOW_E2E_REPOSITORY_BRANCH",
        DEFAULT_REPOSITORY_BRANCH,
    )

    provider: subprocess.Popen[bytes] | None = None
    api: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    run_id: str | None = None

    with tempfile.TemporaryDirectory(prefix="devflow-v25-distributed-") as temp_dir:
        temp_root = Path(temp_dir)
        workspace_root = temp_root / "workspaces"
        provider_records = temp_root / "provider.jsonl"
        provider_log_path = temp_root / "provider.log"
        api_log_path = temp_root / "api.log"
        worker_log_path = temp_root / "worker.log"
        log_paths = {
            "fake provider": provider_log_path,
            "api": api_log_path,
            "dramatiq worker": worker_log_path,
            "provider requests": provider_records,
        }

        child_env = os.environ.copy()
        child_env.update(
            {
                "SILICONFLOW_API_KEY": "devflow-v2.5-e2e-key",
                "DEVFLOW_SILICONFLOW_BASE_URL": f"{PROVIDER_BASE_URL}/v1",
                "DEVFLOW_PLANNER_MODEL": "devflow-e2e-planner",
                "DEVFLOW_DEVELOPER_MODEL": "devflow-e2e-developer",
                "DEVFLOW_REVIEWER_MODEL": "devflow-e2e-reviewer",
                "DEVFLOW_REPAIR_MODEL": "devflow-e2e-repair",
                "DEVFLOW_WORKSPACE_ROOT": str(workspace_root),
                "DEVFLOW_DRAMATIQ_NAMESPACE": f"devflow-v25-{os.getpid()}",
                "DEVFLOW_DRAMATIQ_QUEUE_NAME": "devflow_v25_tasks",
                "DEVFLOW_WORKER_ID": "release-e2e-worker",
                "DEVFLOW_WORKER_LEASE_SECONDS": "30",
                "DEVFLOW_WORKER_HEARTBEAT_INTERVAL_SECONDS": "5",
            }
        )

        try:
            with (
                provider_log_path.open("wb") as provider_log,
                api_log_path.open("wb") as api_log,
                worker_log_path.open("wb") as worker_log,
            ):
                provider = subprocess.Popen(
                    [
                        sys.executable,
                        str(REPOSITORY_ROOT / "scripts" / "fake_openai_provider.py"),
                        "--port",
                        "18080",
                        "--log",
                        str(provider_records),
                    ],
                    cwd=REPOSITORY_ROOT,
                    env=child_env,
                    stdout=provider_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for_json(
                    f"{PROVIDER_BASE_URL}/healthz",
                    process=provider,
                    log_path=provider_log_path,
                    expected={"status": "ok"},
                )

                api = subprocess.Popen(
                    [sys.executable, "-m", "app.api.main"],
                    cwd=BACKEND_ROOT,
                    env=child_env,
                    stdout=api_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_for_json(
                    f"{API_BASE_URL}/healthz",
                    process=api,
                    log_path=api_log_path,
                    expected={"status": "ok"},
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
                    raise DistributedE2EFailure(
                        "Dramatiq worker exited during startup\n"
                        + worker_log_path.read_text(encoding="utf-8", errors="replace")
                    )

                readiness = _request_json("GET", f"{API_BASE_URL}/readyz", timeout=20.0)
                if readiness.get("status") != "READY":
                    raise DistributedE2EFailure(f"API readiness failed: {readiness!r}")

                project = _request_json(
                    "POST",
                    f"{API_BASE_URL}/api/v1/projects",
                    payload={
                        "repository_url": repository_url,
                        "default_branch": repository_branch,
                    },
                    timeout=120.0,
                )
                if not project.get("workspace_ready"):
                    raise DistributedE2EFailure(f"Project workspace is not ready: {project!r}")
                project_id = str(project["project_id"])

                launch = _request_json(
                    "POST",
                    f"{API_BASE_URL}/api/v1/runs/from-requirement",
                    payload={
                        "project_id": project_id,
                        "requirement": (
                            "Create two deterministic V2.5 distributed release marker files in "
                            "dependency order so DevFlow must integrate the first task before "
                            "dispatching the second."
                        ),
                    },
                    timeout=60.0,
                )
                if launch.get("launch_state") != "QUEUED":
                    raise DistributedE2EFailure(f"requirement Run was not queued: {launch!r}")
                task_ids = tuple(launch.get("task_ids") or ())
                if task_ids != TASK_ORDER:
                    raise DistributedE2EFailure(f"unexpected Planner DAG: {launch!r}")
                dispatches = launch.get("dispatches") or []
                if len(dispatches) != 1 or dispatches[0].get("state") != "QUEUED":
                    raise DistributedE2EFailure(f"initial dispatch was not queued: {launch!r}")
                if dispatches[0].get("task_id") != TASK_ORDER[0]:
                    raise DistributedE2EFailure(
                        f"dependent task was dispatched before its prerequisite: {launch!r}"
                    )

                run_id = str(launch["run_id"])
                terminal = _wait_for_terminal_run(run_id)
                if terminal.get("status") != "SUCCEEDED":
                    raise DistributedE2EFailure(
                        f"distributed Run did not succeed: {terminal!r}"
                    )

                _assert_authoritative_result(run_id)
                _assert_provider_transport(provider_records)
        except Exception as exc:
            state = _run_state_diagnostics(run_id) if run_id is not None else ""
            raise DistributedE2EFailure(
                f"{exc}{state}{_diagnostics(log_paths)}"
            ) from exc
        finally:
            _stop(worker)
            _stop(api)
            _stop(provider)

    print(
        "distributed release E2E: HTTP -> PostgreSQL -> Redis -> Dramatiq -> "
        "OpenAI-compatible provider -> verifier -> Git integration -> evidence PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
