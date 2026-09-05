from __future__ import annotations

import asyncio
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
from uuid import UUID

from app.core.settings import Settings
from app.dispatch import DurableDramatiqTaskDispatcher
from app.models.dag import TaskDAG, TaskNode
from app.models.task import TaskContract
from app.models.work_package import PlanningComplexity, TaskBudgetAllocation
from app.persistence import PostgresDAGStore, PostgresDispatchAttemptStore, PostgresRunTokenBudgetStore
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.workers.executor import ManagedProjectWorkspaceResolver

from sqlalchemy import create_engine, text

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
API_BASE_URL = "http://127.0.0.1:8000"
STARTUP_TIMEOUT_SECONDS = 60.0
RUN_TIMEOUT_SECONDS = 1500.0
POLL_SECONDS = 2.0

REQUIREMENT = """
Create a small browser-playable Gomoku demo isolated under examples/gomoku/.

The delivery should naturally separate into multiple work packages for:
- reusable 15x15 Gomoku game logic with legal-move checks, occupied-cell rejection,
  turn switching, reset, and five-in-a-row detection in all four directions;
- a simple browser UI that renders the board, lets two local players click cells,
  shows the current turn/winner, and supports restart;
- deterministic integration tests that verify core rules and the completed demo without
  introducing new external dependencies.

Use concrete writable-file ownership and deterministic verification commands for every
work package. Keep unrelated repository code unchanged.
""".strip()


def _diagnostic_dag() -> TaskDAG:
    core = TaskContract(
        task_id="gomoku-core-logic",
        objective=(
            "Create examples/gomoku/gomoku_core.js as a dependency-free UMD/CommonJS-compatible "
            "15x15 Gomoku engine. Export BOARD_SIZE and GameLogic. GameLogic must expose board, "
            "currentPlayer, winner, isValidMove(row,col), placeMove(row,col), and reset(). "
            "Black moves first; successful moves alternate players; occupied/out-of-range moves "
            "are rejected; winner is detected for horizontal, vertical, and both diagonal five-in-a-row."
        ),
        readable_files=["examples/gomoku/**"],
        writable_files=["examples/gomoku/gomoku_core.js"],
        acceptance_criteria=[
            "Node require() returns GameLogic and BOARD_SIZE=15.",
            "GameLogic rejects out-of-range and occupied cells.",
            "Successful moves alternate black/white turns.",
            "Horizontal, vertical, and both diagonal five-in-a-row set winner.",
            "reset() clears board, winner, and restores black as currentPlayer.",
        ],
        verification_commands=[
            (
                "node -e \"const {GameLogic,BOARD_SIZE}=require('./examples/gomoku/gomoku_core.js');"
                "const g=new GameLogic();if(BOARD_SIZE!==15||!g.isValidMove(7,7)||g.isValidMove(-1,7))process.exit(1);"
                "if(!g.placeMove(7,7)||g.placeMove(7,7)||g.currentPlayer!=='white')process.exit(1);"
                "g.reset();const b=[[0,0],[1,0],[0,1],[1,1],[0,2],[1,2],[0,3],[1,3],[0,4]];"
                "for(const [r,c] of b){if(!g.placeMove(r,c))process.exit(1);}if(g.winner!=='black')process.exit(1);\""
            )
        ],
        max_retries=2,
    )
    ui = TaskContract(
        task_id="gomoku-ui-renderer",
        objective=(
            "Create a dependency-free browser UI under examples/gomoku/index.html and "
            "examples/gomoku/gomoku_ui.js. Render a 15x15 clickable board, alternate visible "
            "black/white stones, show current turn or winner, prevent interaction after a win, "
            "and provide a restart button. The UI must be self-contained and clearly structured "
            "so the integration task can validate its board/status/restart hooks."
        ),
        readable_files=["examples/gomoku/**"],
        writable_files=["examples/gomoku/index.html", "examples/gomoku/gomoku_ui.js"],
        acceptance_criteria=[
            "index.html loads gomoku_ui.js and contains board, status, and restart elements.",
            "gomoku_ui.js creates 225 cells and handles click-driven two-player turns.",
            "A restart action clears rendered stones and restores the initial status.",
        ],
        verification_commands=[
            (
                "node -e \"const fs=require('fs');const h=fs.readFileSync('examples/gomoku/index.html','utf8');"
                "const j=fs.readFileSync('examples/gomoku/gomoku_ui.js','utf8');"
                "for(const x of ['board','status','restart'])if(!h.toLowerCase().includes(x))process.exit(1);"
                "if(!j.includes('225')&&!j.includes('15'))process.exit(1);"
                "if(!/addEventListener/.test(j))process.exit(1);\""
            )
        ],
        max_retries=2,
    )
    integration = TaskContract(
        task_id="gomoku-integration-tests",
        objective=(
            "Create examples/gomoku/gomoku_integration.test.cjs using Node built-in assert only. "
            "Test the delivered core engine for invalid/occupied moves, turn switching, reset, and "
            "all four five-in-a-row directions. Also verify the delivered index.html/gomoku_ui.js "
            "contain the expected board/status/restart wiring. Do not modify core or UI files."
        ),
        readable_files=["examples/gomoku/**"],
        writable_files=["examples/gomoku/gomoku_integration.test.cjs"],
        readonly_files=[
            "examples/gomoku/gomoku_core.js",
            "examples/gomoku/index.html",
            "examples/gomoku/gomoku_ui.js",
        ],
        acceptance_criteria=[
            "The integration test uses only Node built-ins.",
            "All four win directions are tested.",
            "Reset and illegal/occupied move behavior are tested.",
            "Static UI wiring is checked without modifying producer files.",
        ],
        verification_commands=["node examples/gomoku/gomoku_integration.test.cjs"],
        max_retries=2,
    )
    return TaskDAG(
        tasks=(
            TaskNode(
                task=core,
                complexity=PlanningComplexity.MEDIUM,
                budget_allocation=TaskBudgetAllocation(
                    package_id=core.task_id,
                    recommended_token_budget=12_000,
                ),
            ),
            TaskNode(
                task=ui,
                complexity=PlanningComplexity.MEDIUM,
                budget_allocation=TaskBudgetAllocation(
                    package_id=ui.task_id,
                    recommended_token_budget=12_000,
                ),
            ),
            TaskNode(
                task=integration,
                depends_on=(core.task_id, ui.task_id),
                complexity=PlanningComplexity.MEDIUM,
                budget_allocation=TaskBudgetAllocation(
                    package_id=integration.task_id,
                    recommended_token_budget=12_000,
                ),
            ),
        )
    )


async def _launch_fixed_dag(project_id: str) -> dict[str, Any]:
    settings = Settings()
    if settings.database_url is None:
        raise DiagnosticFailure("DEVFLOW_DATABASE_URL is required")
    project_uuid = UUID(project_id)
    resolver = ManagedProjectWorkspaceResolver(settings.workspace_root / "repos")
    workspace = resolver.resolve(project_uuid)
    base_commit = workspace.head_commit()
    dag = _diagnostic_dag()

    evidence_store = ProjectAwarePostgresEvidenceStore.from_url(settings.database_url)
    dag_store = PostgresDAGStore.from_url(settings.database_url)
    ledger = PostgresDispatchAttemptStore.from_url(settings.database_url)
    budget_store = PostgresRunTokenBudgetStore.from_url(
        settings.database_url,
        default_total_budget_tokens=settings.run_token_budget_tokens,
        adaptive_package_budget_enabled=settings.adaptive_package_budget_enabled,
        token_estimate_safety_factor=settings.token_estimate_safety_factor,
    )
    from app.workers.tasks import execute_devflow_task

    dispatcher = DurableDramatiqTaskDispatcher(
        run_store=evidence_store,
        ledger=ledger,
        actor=execute_devflow_task,
    )
    try:
        run_id = await dag_store.start_run(
            project_id=project_uuid,
            dag=dag,
            base_commit=base_commit,
        )
        await budget_store.initialize(
            run_id,
            total_budget_tokens=settings.run_token_budget_tokens,
        )
        await budget_store.initialize_hierarchy(
            run_id=run_id,
            dag=dag,
            developer_max_output_tokens=settings.developer_max_output_tokens,
        )
        root_ids = tuple(
            task_id for task_id in dag.topological_order() if not dag.node(task_id).depends_on
        )
        receipts = []
        for task_id in root_ids:
            receipt = await dispatcher.dispatch(run_id=run_id, task_id=task_id)
            receipts.append(
                {
                    "task_id": task_id,
                    "dispatch_id": str(receipt.dispatch_id),
                    "broker_message_id": receipt.broker_message_id,
                    "queue_name": receipt.queue_name,
                }
            )
        return {
            "run_id": str(run_id),
            "project_id": project_id,
            "base_commit": base_commit,
            "task_ids": dag.topological_order(),
            "initial_ready_task_ids": list(root_ids),
            "launch_state": "QUEUED",
            "dispatches": receipts,
            "planner_bypassed_for_budget_isolation": True,
        }
    finally:
        await dispatcher.dispose()
        await budget_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


class DiagnosticFailure(RuntimeError):
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
    timeout: float = 20.0,
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
            decoded = json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DiagnosticFailure(f"{method} {url} HTTP {exc.code}: {detail}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise DiagnosticFailure(f"{method} {url} failed: {exc}") from exc
    if not isinstance(decoded, dict):
        raise DiagnosticFailure(f"{method} {url} did not return a JSON object")
    return decoded


def _wait_ready(url: str, *, process: subprocess.Popen[bytes], log_path: Path) -> dict[str, Any]:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DiagnosticFailure(
                f"process exited before {url} became ready\n"
                + log_path.read_text(encoding="utf-8", errors="replace")
            )
        try:
            return _request_json("GET", url, timeout=5.0)
        except DiagnosticFailure as exc:
            last_error = exc
            time.sleep(1.0)
    raise DiagnosticFailure(
        f"timed out waiting for {url}: {last_error}\n"
        + log_path.read_text(encoding="utf-8", errors="replace")
    )


def _safe_get(url: str) -> dict[str, Any]:
    try:
        return _request_json("GET", url)
    except Exception as exc:
        return {"diagnostic_error": f"{type(exc).__name__}: {exc}"}


def _sync_database_url() -> str:
    # The locked backend environment ships psycopg v3, not psycopg2. SQLAlchemy supports
    # synchronous access through postgresql+psycopg, so keep the configured driver intact.
    return os.environ["DEVFLOW_DATABASE_URL"]


def _db_rows(run_id: str, table: str) -> list[dict[str, Any]]:
    engine = create_engine(_sync_database_url())
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(f"SELECT * FROM {table} WHERE run_id = :run_id ORDER BY id"),
                {"run_id": run_id},
            )
            rows = []
            for item in result.mappings():
                row = {}
                for key, value in item.items():
                    if hasattr(value, "isoformat"):
                        row[key] = value.isoformat()
                    elif isinstance(value, (str, int, float, bool)) or value is None:
                        row[key] = value
                    else:
                        row[key] = str(value)
                rows.append(row)
            return rows
    finally:
        engine.dispose()


def _metrics_snapshot(run_id: str) -> dict[str, Any]:
    metrics = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/metrics")
    budget = metrics.get("token_budget") if isinstance(metrics, dict) else None
    if not isinstance(budget, dict):
        return {"at": time.time(), "metrics": metrics}
    return {
        "at": time.time(),
        "status": metrics.get("status"),
        "used_total_tokens": budget.get("used_total_tokens"),
        "used_prompt_tokens": budget.get("used_prompt_tokens"),
        "used_completion_tokens": budget.get("used_completion_tokens"),
        "reserved_tokens": budget.get("reserved_tokens"),
        "budget_status": budget.get("status"),
        "roles": budget.get("roles"),
        "work_packages": budget.get("work_packages"),
        "cost_observation_count": budget.get("cost_observation_count"),
    }


def _wait_terminal(run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    snapshots: list[dict[str, Any]] = []
    last_signature: tuple[Any, ...] | None = None
    last_run: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_run = _request_json("GET", f"{API_BASE_URL}/api/v1/runs/{run_id}")
        snap = _metrics_snapshot(run_id)
        signature = (
            snap.get("status"),
            snap.get("used_total_tokens"),
            snap.get("reserved_tokens"),
            snap.get("cost_observation_count"),
        )
        if signature != last_signature:
            snapshots.append(snap)
            last_signature = signature
        if last_run.get("status") in {"SUCCEEDED", "FAILED"}:
            return last_run, snapshots
        time.sleep(POLL_SECONDS)
    raise DiagnosticFailure(
        f"run {run_id} did not reach terminal state; last={json.dumps(last_run, ensure_ascii=False)}"
    )


def _task_details(run_id: str, dag: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for task_id in dag.get("topological_order") or ():
        details[str(task_id)] = _safe_get(
            f"{API_BASE_URL}/api/v1/runs/{run_id}/tasks/{task_id}"
        )
    return details


def _analyze(metrics: dict[str, Any], observations: list[dict[str, Any]]) -> dict[str, Any]:
    budget = metrics.get("token_budget") if isinstance(metrics, dict) else {}
    if not isinstance(budget, dict):
        budget = {}
    settled = [row for row in observations if row.get("actual_prompt_tokens") is not None]
    estimated = sum(int(row.get("request_estimated_tokens") or 0) for row in settled)
    actual_prompt = sum(int(row.get("actual_prompt_tokens") or 0) for row in settled)
    actual_completion = sum(int(row.get("actual_completion_tokens") or 0) for row in settled)
    tool_args = sum(int(row.get("tool_argument_tokens") or 0) for row in settled)
    tool_results = sum(int(row.get("tool_result_tokens") or 0) for row in settled)
    compacted = sum(int(row.get("compacted_tool_argument_tokens") or 0) for row in settled)
    no_progress = sum(not bool(row.get("has_real_progress")) for row in settled)
    largest_prompt = max(
        settled,
        key=lambda row: int(row.get("actual_prompt_tokens") or 0),
        default=None,
    )
    by_task_role: dict[str, dict[str, int]] = {}
    for row in settled:
        key = f"{row.get('task_id')}::{row.get('role')}"
        entry = by_task_role.setdefault(
            key,
            {"turns": 0, "estimated_prompt": 0, "actual_prompt": 0, "completion": 0},
        )
        entry["turns"] += 1
        entry["estimated_prompt"] += int(row.get("request_estimated_tokens") or 0)
        entry["actual_prompt"] += int(row.get("actual_prompt_tokens") or 0)
        entry["completion"] += int(row.get("actual_completion_tokens") or 0)
    return {
        "run_budget": {
            "total": budget.get("total_budget_tokens"),
            "used": budget.get("used_total_tokens"),
            "prompt": budget.get("used_prompt_tokens"),
            "completion": budget.get("used_completion_tokens"),
            "reserved": budget.get("reserved_tokens"),
            "status": budget.get("status"),
        },
        "settled_developer_repair_turns": len(settled),
        "estimated_prompt_sum": estimated,
        "actual_prompt_sum": actual_prompt,
        "actual_completion_sum": actual_completion,
        "estimator_delta_sum": estimated - actual_prompt,
        "estimator_delta_ratio": (
            (estimated - actual_prompt) / actual_prompt if actual_prompt else None
        ),
        "tool_argument_tokens_sum": tool_args,
        "tool_result_tokens_sum": tool_results,
        "compacted_tool_argument_tokens_sum": compacted,
        "no_real_progress_turns": no_progress,
        "largest_actual_prompt_turn": largest_prompt,
        "by_task_role": by_task_role,
    }


def main() -> int:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        raise DiagnosticFailure("DASHSCOPE_API_KEY is required")
    if not os.environ.get("DEVFLOW_DATABASE_URL"):
        raise DiagnosticFailure("DEVFLOW_DATABASE_URL is required")
    dramatiq = shutil.which("dramatiq")
    if dramatiq is None:
        raise DiagnosticFailure("dramatiq executable is not available")

    report_path = Path(os.environ.get("DEVFLOW_DIAGNOSTIC_REPORT", "token-budget-diagnostic.json"))
    repository_url = os.environ.get(
        "DEVFLOW_DIAGNOSTIC_REPOSITORY_URL",
        "https://github.com/xinxin1101/Muti_Agent.git",
    )
    repository_branch = os.environ.get("DEVFLOW_DIAGNOSTIC_REPOSITORY_BRANCH", "xin_01")

    report: dict[str, Any] = {
        "diagnostic": "real-qwen-multipackage-token-budget",
        "model": os.environ.get("DEVFLOW_DEVELOPER_MODEL"),
        "run_budget": os.environ.get("DEVFLOW_RUN_TOKEN_BUDGET_TOKENS"),
        "requirement": REQUIREMENT,
        "status": "STARTING",
    }

    api: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    run_id: str | None = None

    with tempfile.TemporaryDirectory(prefix="devflow-real-budget-diagnostic-") as temp_dir:
        root = Path(temp_dir)
        api_log_path = root / "api.log"
        worker_log_path = root / "worker.log"
        workspace_root = root / "workspaces"
        namespace = f"budget-diagnostic-{os.getpid()}"
        os.environ["DEVFLOW_WORKSPACE_ROOT"] = str(workspace_root)
        os.environ["DEVFLOW_DRAMATIQ_NAMESPACE"] = namespace
        os.environ["DEVFLOW_DRAMATIQ_QUEUE_NAME"] = "budget_diagnostic_tasks"
        os.environ["DEVFLOW_WORKER_ID"] = "budget-diagnostic-worker"
        os.environ["DEVFLOW_WORKER_LEASE_SECONDS"] = "45"
        os.environ["DEVFLOW_WORKER_HEARTBEAT_INTERVAL_SECONDS"] = "8"
        child_env = os.environ.copy()
        child_env.update(
            {
                "DEVFLOW_WORKSPACE_ROOT": str(workspace_root),
                "DEVFLOW_DRAMATIQ_NAMESPACE": f"budget-diagnostic-{os.getpid()}",
                "DEVFLOW_DRAMATIQ_QUEUE_NAME": "budget_diagnostic_tasks",
                "DEVFLOW_WORKER_ID": "budget-diagnostic-worker",
                "DEVFLOW_WORKER_LEASE_SECONDS": "45",
                "DEVFLOW_WORKER_HEARTBEAT_INTERVAL_SECONDS": "8",
            }
        )

        try:
            with api_log_path.open("wb") as api_log, worker_log_path.open("wb") as worker_log:
                api = subprocess.Popen(
                    [sys.executable, "-m", "app.api.main"],
                    cwd=BACKEND_ROOT,
                    env=child_env,
                    stdout=api_log,
                    stderr=subprocess.STDOUT,
                )
                _wait_ready(
                    f"{API_BASE_URL}/healthz",
                    process=api,
                    log_path=api_log_path,
                )

                worker = subprocess.Popen(
                    [dramatiq, "app.workers.tasks"],
                    cwd=BACKEND_ROOT,
                    env=child_env,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                )
                time.sleep(2)
                if worker.poll() is not None:
                    raise DiagnosticFailure(
                        "Dramatiq worker exited during startup\n"
                        + worker_log_path.read_text(encoding="utf-8", errors="replace")
                    )

                report["readiness"] = _safe_get(f"{API_BASE_URL}/readyz")
                project = _request_json(
                    "POST",
                    f"{API_BASE_URL}/api/v1/projects",
                    payload={
                        "repository_url": repository_url,
                        "default_branch": repository_branch,
                    },
                    timeout=180.0,
                )
                report["project"] = project
                launch = asyncio.run(_launch_fixed_dag(str(project["project_id"])))
                report["launch"] = launch
                run_id = str(launch["run_id"])
                terminal, snapshots = _wait_terminal(run_id)
                report["run"] = terminal
                report["budget_timeline"] = snapshots
                report["dag"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/dag")
                report["metrics"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/metrics")
                report["trace"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/trace")
                report["tasks"] = _task_details(run_id, report["dag"])
                report["cost_observations"] = _db_rows(
                    run_id, "run_task_cost_observations"
                )
                report["budget_decisions"] = _db_rows(
                    run_id, "run_token_budget_decisions"
                )
                report["analysis"] = _analyze(
                    report["metrics"], report["cost_observations"]
                )
                report["status"] = "COMPLETE"
        except Exception as exc:
            report["status"] = "DIAGNOSTIC_ERROR"
            report["error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:8000],
            }
            if run_id is not None:
                report["run"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}")
                report["dag"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/dag")
                report["metrics"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/metrics")
                report["trace"] = _safe_get(f"{API_BASE_URL}/api/v1/runs/{run_id}/trace")
                try:
                    report["cost_observations"] = _db_rows(
                        run_id, "run_task_cost_observations"
                    )
                    report["budget_decisions"] = _db_rows(
                        run_id, "run_token_budget_decisions"
                    )
                    report["analysis"] = _analyze(
                        report["metrics"], report["cost_observations"]
                    )
                except Exception as db_exc:
                    report["database_diagnostic_error"] = str(db_exc)
        finally:
            _stop(worker)
            _stop(api)
            report["api_log"] = (
                api_log_path.read_text(encoding="utf-8", errors="replace")
                if api_log_path.exists()
                else ""
            )
            report["worker_log"] = (
                worker_log_path.read_text(encoding="utf-8", errors="replace")
                if worker_log_path.exists()
                else ""
            )
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
            )

    print(json.dumps({
        "status": report.get("status"),
        "model": report.get("model"),
        "run_status": (report.get("run") or {}).get("status"),
        "analysis": report.get("analysis"),
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    # A business Run may legitimately fail because the purpose is to capture the failure.
    # Only infrastructure/setup failures before a useful report are treated as workflow errors.
    return 0 if report.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())