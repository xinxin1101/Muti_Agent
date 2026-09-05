from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

# Reuse the repository's already-proven real-provider acceptance helpers while keeping this
# experiment harness outside the A/B runtime checkout. The workflow copies this file to /tmp
# before checking out the exact P1.5.1/P1.5.2 baseline SHA.
sys.path.insert(0, str(Path.cwd() / "scripts"))

from real_gomoku_acceptance import (  # noqa: E402
    _budget_audit,
    _budgeted,
    _developer,
    _persist_acceptance_run,
    _repair,
    _reviewer,
    _run_diagnostics,
    _runner,
    _store,
)

from app.core.settings import Settings  # noqa: E402
from app.models.agent import AgentRole  # noqa: E402
from app.models.task import TaskContract  # noqa: E402
from app.models.trace import TraceSpanKind  # noqa: E402
from app.providers.siliconflow import SiliconFlowDriver  # noqa: E402
from app.trace.collector import TaskTraceCollector  # noqa: E402
from app.workspace import LocalGitWorkspace  # noqa: E402

INDEX = "src/index.html"
UI = "src/gomoku_ui.js"
RUN_BUDGET = 50_000

VERIFY = (
    'python3 -c "from pathlib import Path; '
    "h=Path('src/index.html').read_text(encoding='utf-8'); "
    "j=Path('src/gomoku_ui.js').read_text(encoding='utf-8'); "
    "assert 'gomoku_ui.js' in h; assert 'gomoku-board' in h; "
    "assert len(j) >= 200; assert '15' in j; "
    "assert ('addEventListener' in j or 'onclick' in j)\""
)


def _git(root: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _repository(root: Path) -> LocalGitWorkspace:
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / INDEX).write_text(
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head><meta charset=\"utf-8\"><title>五子棋</title></head>\n"
        '<body><main id="app"><h1>五子棋</h1></main></body>\n'
        "</html>\n",
        encoding="utf-8",
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "p153-acceptance@devflow.local")
    _git(root, "config", "user.name", "DevFlow P1.5.3 Acceptance")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "gomoku ui acceptance baseline")
    return LocalGitWorkspace(root)


def _task() -> TaskContract:
    return TaskContract(
        task_id="gomoku-ui",
        objective=(
            "在现有 src/index.html 基础上完成一个可直接在浏览器运行的 15x15 五子棋界面，"
            "并把交互逻辑放入 src/gomoku_ui.js。页面应显示棋盘，支持点击空交叉点轮流落黑白棋，"
            "能够阻止同一位置重复落子，并提供重新开始按钮。不要创建第三个实现文件。"
        ),
        readable_files=["src/**"],
        writable_files=[INDEX, UI],
        readonly_files=[],
        acceptance_criteria=[
            "src/index.html 和 src/gomoku_ui.js 都必须形成相对任务起点的候选改动。",
            "index.html 包含 gomoku-board 棋盘容器并加载 gomoku_ui.js。",
            "gomoku_ui.js 实现 15x15 棋盘与点击落子交互，并避免重复占用已有位置。",
            "页面提供重新开始能力，且实现不依赖第三方网络资源。",
        ],
        verification_commands=[VERIFY],
        max_retries=2,
    )


def _developer_timeline(trace: TaskTraceCollector) -> list[dict[str, Any]]:
    if trace.empty:
        return []
    timeline: list[dict[str, Any]] = []
    for span in trace.batch().spans:
        if span.kind is not TraceSpanKind.AGENT_TURN or span.agent_role is not AgentRole.DEVELOPER:
            continue
        timeline.append(
            {
                "iteration": span.iteration,
                "prompt_tokens": span.prompt_tokens,
                "completion_tokens": span.completion_tokens,
                "total_tokens": span.total_tokens,
                "tool_call_count": span.tool_call_count,
                "finish_reason": span.finish_reason,
                "has_workspace_patch": span.has_workspace_patch,
                "turn_made_progress": span.turn_made_progress,
                "changed_files_this_turn": list(span.changed_files_this_turn),
                "consecutive_mutation_turns": span.consecutive_mutation_turns,
                "same_file_mutation_streak": span.same_file_mutation_streak,
                "convergence_nudge_triggered": span.convergence_nudge_triggered,
                "candidate_readiness_known": getattr(span, "candidate_readiness_known", None),
                "candidate_ready": getattr(span, "candidate_ready", None),
                "missing_required_deliverables": list(
                    getattr(span, "missing_required_deliverables", ())
                ),
                "deliverable_progress": getattr(span, "deliverable_progress", None),
                "deliverable_completion_mode": getattr(
                    span, "deliverable_completion_mode", None
                ),
                "deliverable_convergence_violations": getattr(
                    span, "deliverable_convergence_violations", None
                ),
            }
        )
    return timeline


def _tool_timeline(trace: TaskTraceCollector) -> list[dict[str, Any]]:
    if trace.empty:
        return []
    items: list[dict[str, Any]] = []
    for span in trace.batch().spans:
        if span.kind is not TraceSpanKind.TOOL_CALL or span.agent_role is not AgentRole.DEVELOPER:
            continue
        items.append(
            {
                "iteration": span.iteration,
                "tool_name": span.tool_name,
                "status": span.status.value,
                "tool_error_code": None if span.tool_error_code is None else span.tool_error_code.value,
            }
        )
    return items


async def _run(label: str, baseline_sha: str, report_path: Path) -> int:
    settings = Settings()
    report: dict[str, Any] = {
        "experiment": "P1.5.3 Real Gomoku UI A/B",
        "label": label,
        "baseline_sha": baseline_sha,
        "model": settings.developer_model,
        "run_budget_tokens": RUN_BUDGET,
        "temperature": 0.0,
        "runtime_v3": True,
        "task_contract": {
            "task_id": "gomoku-ui",
            "writable_files": [INDEX, UI],
            "candidate_readiness_expected": True,
            "verification_command": VERIFY,
        },
    }
    raw_driver: SiliconFlowDriver | None = None
    store = None
    try:
        if settings.siliconflow_api_key is None:
            raise RuntimeError("DASHSCOPE_API_KEY/SILICONFLOW_API_KEY is not configured")
        if settings.database_url is None:
            raise RuntimeError("DEVFLOW_DATABASE_URL is required")

        raw_driver = SiliconFlowDriver.from_settings(settings)
        with tempfile.TemporaryDirectory(prefix=f"p153-{label}-") as temp:
            workspace = _repository(Path(temp) / "repo")
            task = _task()
            run_id = uuid4()
            project_id = uuid4()
            await _persist_acceptance_run(
                settings,
                run_id=run_id,
                project_id=project_id,
                task=task,
                base_commit=workspace.head_commit(),
                repository_url=f"https://p153.acceptance.devflow.local/{project_id}",
            )
            store = await _store(settings, run_id=run_id, total_tokens=RUN_BUDGET)
            budgeted = _budgeted(
                raw_driver=raw_driver,
                store=store,
                run_id=run_id,
                task_id=task.task_id,
                settings=settings,
            )
            trace = TaskTraceCollector(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=uuid4(),
                generation=1,
            )
            runner = _runner(
                settings,
                developer=_developer(
                    settings,
                    driver=budgeted,
                    model=settings.developer_model,
                ),
                repair=_repair(settings, driver=budgeted),
                reviewer=_reviewer(settings, driver=budgeted),
            )
            result = await runner.run(task, workspace=workspace, trace=trace)
            budget = await store.snapshot(run_id)

            report["outcome"] = _run_diagnostics(result, budget)
            report["token_audit"] = _budget_audit(budget)
            report["developer_timeline"] = _developer_timeline(trace)
            report["developer_tool_timeline"] = _tool_timeline(trace)
            report["final_changed_files"] = result.changed_files
            report["final_head"] = workspace.head_commit()
            report["experiment_status"] = "COMPLETE"
        return 0
    except Exception as exc:
        report["experiment_status"] = "HARNESS_ERROR"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:4000]}
        return 1
    finally:
        if store is not None:
            await store.dispose()
        if raw_driver is not None:
            await raw_driver.dispose()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.label, args.baseline_sha, args.report))


if __name__ == "__main__":
    raise SystemExit(main())
