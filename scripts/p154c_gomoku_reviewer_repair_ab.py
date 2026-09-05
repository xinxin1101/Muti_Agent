from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

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
from app.models.task import TaskContract  # noqa: E402
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


class RecordingDriver:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.records: list[dict[str, Any]] = []

    async def complete(self, request):  # type: ignore[no-untyped-def]
        response = await self.inner.complete(request)
        self.records.append(
            {
                "role": request.role.value,
                "execution_iteration": request.execution_iteration,
                "prompt_message_count": len(request.messages),
                "request_messages": [
                    {
                        "role": message.role.value,
                        "content": message.content[:10_000],
                    }
                    for message in request.messages
                ],
                "max_output_tokens": request.max_output_tokens,
                "response_model": response.model,
                "finish_reason": response.finish_reason,
                "usage": response.usage.model_dump(mode="json"),
                "content": response.content[:12_000],
                "tool_calls": [
                    {
                        "name": call.name,
                        "arguments": call.arguments[:6_000],
                    }
                    for call in response.tool_calls
                ],
            }
        )
        return response

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def repository(root: Path) -> LocalGitWorkspace:
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / INDEX).write_text(
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        '<head><meta charset="utf-8"><title>五子棋</title></head>\n'
        '<body><main id="app"><h1>五子棋</h1></main></body>\n'
        "</html>\n",
        encoding="utf-8",
    )
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "p154c@devflow.local")
    git(root, "config", "user.name", "DevFlow P1.5.4-C")
    git(root, "add", ".")
    git(root, "commit", "-m", "gomoku ui p154c baseline")
    return LocalGitWorkspace(root)


def task() -> TaskContract:
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


def trace_agent_turns(trace: TaskTraceCollector) -> list[dict[str, Any]]:
    if trace.empty:
        return []
    rows: list[dict[str, Any]] = []
    for span in trace.batch().spans:
        if getattr(span, "agent_role", None) is None:
            continue
        if getattr(span, "prompt_tokens", None) is None:
            continue
        rows.append(
            {
                "name": span.name,
                "role": span.agent_role.value,
                "iteration": span.iteration,
                "prompt_tokens": span.prompt_tokens,
                "completion_tokens": span.completion_tokens,
                "total_tokens": span.total_tokens,
                "tool_call_count": span.tool_call_count,
                "finish_reason": span.finish_reason,
            }
        )
    return rows


def _tool_path(call: dict[str, Any]) -> str | None:
    raw = call.get("arguments")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    path = payload.get("path")
    if isinstance(path, str):
        return path
    patch = payload.get("patch")
    if isinstance(patch, str):
        if UI in patch:
            return UI
        if INDEX in patch:
            return INDEX
    return None


def repair_forensic(records: list[dict[str, Any]]) -> dict[str, Any]:
    repair_records = [record for record in records if record.get("role") == "Repair"]
    sequence: list[dict[str, Any]] = []
    for model_call, record in enumerate(repair_records, start=1):
        for call in record.get("tool_calls", []):
            sequence.append(
                {
                    "model_call": model_call,
                    "name": call.get("name"),
                    "path": _tool_path(call),
                    "arguments": call.get("arguments", "")[:2_000],
                }
            )

    first = repair_records[0] if repair_records else None
    first_request_text = ""
    first_tool_calls: list[dict[str, Any]] = []
    if first is not None:
        first_request_text = "\n".join(
            str(message.get("content", "")) for message in first.get("request_messages", [])
        )
        first_tool_calls = [
            {
                "name": call.get("name"),
                "path": _tool_path(call),
                "arguments": call.get("arguments", "")[:2_000],
            }
            for call in first.get("tool_calls", [])
        ]

    return {
        "repair_model_calls": len(repair_records),
        "first_request_has_deterministic_prefetch": (
            "Deterministic Repair prefetch" in first_request_text
        ),
        "first_request_has_semantic_review_issue": "SEMANTIC_REVIEW_ISSUE" in first_request_text,
        "first_request_mentions_ui_path": UI in first_request_text,
        "first_request_mentions_reported_line": "line=" in first_request_text,
        "first_tool_calls": first_tool_calls,
        "tool_sequence": sequence,
        "index_read_count": sum(
            1
            for item in sequence
            if item["name"] in {"read_range", "read_symbol"} and item["path"] == INDEX
        ),
        "ui_read_count": sum(
            1
            for item in sequence
            if item["name"] in {"read_range", "read_symbol"} and item["path"] == UI
        ),
        "ui_mutation_count": sum(
            1
            for item in sequence
            if item["name"] in {"apply_patch", "write_file"} and item["path"] == UI
        ),
        "first_call_mutates_ui": any(
            call["name"] in {"apply_patch", "write_file"} and call["path"] == UI
            for call in first_tool_calls
        ),
    }


def read_if_exists(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[:limit]


async def run(report_path: Path, *, label: str, source_sha: str) -> int:
    settings = Settings()
    raw: SiliconFlowDriver | None = None
    store = None
    report: dict[str, Any] = {
        "experiment": "P1.5.4-C Real Gomoku Reviewer-Repair Convergence Validation",
        "variant": label,
        "source_sha": source_sha,
        "model": settings.developer_model,
        "temperature": 0.0,
        "run_budget_tokens": RUN_BUDGET,
    }
    try:
        if settings.siliconflow_api_key is None or settings.database_url is None:
            raise RuntimeError("provider/database configuration missing")
        actual_sha = git(Path.cwd(), "rev-parse", "HEAD")
        report["actual_checkout_sha"] = actual_sha
        if actual_sha != source_sha:
            raise RuntimeError(f"checkout drift: expected {source_sha}, got {actual_sha}")

        raw = SiliconFlowDriver.from_settings(settings)
        with tempfile.TemporaryDirectory(prefix=f"p154c-{label.lower()}-") as temp:
            workspace = repository(Path(temp) / "repo")
            contract = task()
            run_id, project_id = uuid4(), uuid4()
            await _persist_acceptance_run(
                settings,
                run_id=run_id,
                project_id=project_id,
                task=contract,
                base_commit=workspace.head_commit(),
                repository_url=f"https://p154c.acceptance.devflow.local/{label}/{project_id}",
            )
            store = await _store(settings, run_id=run_id, total_tokens=RUN_BUDGET)
            budgeted = _budgeted(
                raw_driver=raw,
                store=store,
                run_id=run_id,
                task_id=contract.task_id,
                settings=settings,
            )
            recording = RecordingDriver(budgeted)
            trace = TaskTraceCollector(
                run_id=run_id,
                task_id=contract.task_id,
                dispatch_id=uuid4(),
                generation=1,
            )
            runner = _runner(
                settings,
                developer=_developer(settings, driver=recording, model=settings.developer_model),
                repair=_repair(settings, driver=recording),
                reviewer=_reviewer(settings, driver=recording),
            )
            result = await runner.run(contract, workspace=workspace, trace=trace)
            budget = await store.snapshot(run_id)
            report["outcome"] = _run_diagnostics(result, budget)
            report["reviews"] = [review.model_dump(mode="json") for review in result.reviews]
            report["repairs"] = [repair.model_dump(mode="json") for repair in result.repairs]
            report["model_records"] = recording.records
            report["repair_forensic"] = repair_forensic(recording.records)
            report["trace_agent_turns"] = trace_agent_turns(trace)
            report["token_audit"] = _budget_audit(budget)
            report["final_diff"] = workspace.unified_diff()[:30_000]
            report["final_index"] = read_if_exists(workspace.root / INDEX, 20_000)
            report["final_ui"] = read_if_exists(workspace.root / UI, 30_000)
            report["status"] = "COMPLETE"
        return 0
    except Exception as exc:
        report["status"] = "HARNESS_ERROR"
        report["error"] = {"type": type(exc).__name__, "message": str(exc)[:4_000]}
        return 1
    finally:
        if store is not None:
            await store.dispose()
        if raw is not None:
            await raw.dispose()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--label", choices=("A", "B"), required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.report, label=args.label, source_sha=args.source_sha))


if __name__ == "__main__":
    raise SystemExit(main())
