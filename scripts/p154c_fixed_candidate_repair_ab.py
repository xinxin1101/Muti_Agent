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
from app.models.agent import AgentResponse, TokenUsage  # noqa: E402
from app.models.task import TaskContract  # noqa: E402
from app.models.tools import ToolCall  # noqa: E402
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

FIXED_INDEX = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>五子棋</title></head>
<body>
  <main id="app">
    <h1>五子棋</h1>
    <div id="gomoku-board"></div>
    <button id="restart-btn">重新开始</button>
  </main>
  <script src="gomoku_ui.js"></script>
</body>
</html>
"""

FIXED_UI = """(function() {
  const BOARD_SIZE = 15;
  const CELL_SIZE = 30;
  const PADDING = 20;

  let board = [];
  let currentPlayer = 1; // 1 for black, 2 for white
  let gameOver = false;

  function initBoard() {
    board = Array(BOARD_SIZE).fill(null).map(() => Array(BOARD_SIZE).fill(0));
    currentPlayer = 1;
    gameOver = false;
  }

  function createBoardElement() {
    const container = document.getElementById('gomoku-board');
    if (!container) return;

    const canvas = document.createElement('canvas');
    canvas.width = BOARD_SIZE * CELL_SIZE + PADDING * 2;
    canvas.height = BOARD_SIZE * CELL_SIZE + PADDING * 2;
    canvas.style.cursor = 'pointer';
    container.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    drawBoard(ctx);

    canvas.addEventListener('click', handleCanvasClick.bind(null, ctx, canvas));
  }

  function drawBoard(ctx) {
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 1;

    for (let i = 0; i < BOARD_SIZE; i++) {
      ctx.beginPath();
      ctx.moveTo(PADDING + i * CELL_SIZE, PADDING);
      ctx.lineTo(PADDING + i * CELL_SIZE, PADDING + (BOARD_SIZE - 1) * CELL_SIZE);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(PADDING, PADDING + i * CELL_SIZE);
      ctx.lineTo(PADDING + (BOARD_SIZE - 1) * CELL_SIZE, PADDING + i * CELL_SIZE);
      ctx.stroke();
    }

    const stars = [3, 7, 11];
    for (const x of stars) {
      for (const y of stars) {
        ctx.beginPath();
        ctx.arc(PADDING + x * CELL_SIZE, PADDING + y * CELL_SIZE, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#000';
        ctx.fill();
      }
    }
  }

  function drawPiece(ctx, row, col, player) {
    const x = PADDING + col * CELL_SIZE;
    const y = PADDING + row * CELL_SIZE;
    const radius = CELL_SIZE / 2 - 2;

    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = player === 1 ? '#000' : '#fff';
    ctx.fill();
    ctx.strokeStyle = '#000';
    ctx.stroke();
  }

  function handleCanvasClick(ctx, canvas, event) {
    if (gameOver) return;

    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left - PADDING;
    const y = event.clientY - rect.top - PADDING;

    const col = Math.round(x / CELL_SIZE);
    const row = Math.round(y / CELL_SIZE);

    if (row < 0 || row >= BOARD_SIZE || col < 0 || col >= BOARD_SIZE) return;
    if (board[row][col] !== 0) return;

    board[row][col] = currentPlayer;
    drawPiece(ctx, row, col, currentPlayer);

    if (checkWin(row, col, currentPlayer)) {
      gameOver = true;
      setTimeout(() => alert(currentPlayer === 1 ? '黑棋获胜!' : '白棋获胜!'), 10);
      return;
    }

    currentPlayer = currentPlayer === 1 ? 2 : 1;
  }

  function checkWin(row, col, player) {
    const directions = [
      [0, 1], [1, 0], [1, 1], [1, -1]
    ];

    for (const [dx, dy] of directions) {
      let count = 1;

      for (let i = 1; i < 5; i++) {
        const r = row + dx * i;
        const c = col + dy * i;
        if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE || board[r][c] !== player) break;
        count++;
      }

      for (let i = 1; i < 5; i++) {
        const r = row - dx * i;
        const c = col - dy * i;
        if (r < 0 || r >= BOARD_SIZE || c < 0 || c >= BOARD_SIZE || board[r][c] !== player) break;
        count++;
      }

      if (count >= 5) return true;
    }

    return false;
  }

  function resetGame() {
    initBoard();
    const canvas = document.querySelector('#gomoku-board canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      drawBoard(ctx);
    }
  }

  document.addEventListener('DOMContentLoaded', function() {
    initBoard();
    createBoardElement();

    const resetBtn = document.createElement('button');
    resetBtn.textContent = '重新开始';
    resetBtn.onclick = resetGame;
    document.getElementById('app').appendChild(resetBtn);
  });
})();
"""


class ScriptedCandidateDriver:
    """Emit the exact semantic-bad candidate observed in the first P1.5.4-C B run."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        del request
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                model="p154c/scripted-fixed-candidate",
                content="",
                tool_calls=[
                    ToolCall(
                        id="fixed-index",
                        name="write_file",
                        arguments=json.dumps({"path": INDEX, "content": FIXED_INDEX}),
                    ),
                    ToolCall(
                        id="fixed-ui",
                        name="write_file",
                        arguments=json.dumps({"path": UI, "content": FIXED_UI}),
                    ),
                ],
                usage=TokenUsage(),
                latency_ms=0,
                finish_reason="tool_calls",
            )
        return AgentResponse(
            model="p154c/scripted-fixed-candidate",
            content="已修改文件: src/index.html, src/gomoku_ui.js",
            tool_calls=[],
            usage=TokenUsage(),
            latency_ms=0,
            finish_reason="stop",
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
                "request_messages": [
                    {"role": message.role.value, "content": message.content[:12_000]}
                    for message in request.messages
                ],
                "usage": response.usage.model_dump(mode="json"),
                "content": response.content[:12_000],
                "finish_reason": response.finish_reason,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments[:8_000]}
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
    git(root, "config", "user.email", "p154c-fixed@devflow.local")
    git(root, "config", "user.name", "DevFlow P1.5.4-C Fixed")
    git(root, "add", ".")
    git(root, "commit", "-m", "gomoku ui fixed-candidate baseline")
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
    repair_records = [record for record in records if record.get("role") == "repair"]
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

    first_text = ""
    first_calls: list[dict[str, Any]] = []
    if repair_records:
        first = repair_records[0]
        first_text = "\n".join(
            str(message.get("content", "")) for message in first.get("request_messages", [])
        )
        first_calls = [
            {"name": call.get("name"), "path": _tool_path(call)}
            for call in first.get("tool_calls", [])
        ]

    return {
        "repair_model_calls": len(repair_records),
        "first_request_has_deterministic_prefetch": "Deterministic Repair prefetch" in first_text,
        "first_request_has_semantic_review_issue": "SEMANTIC_REVIEW_ISSUE" in first_text,
        "first_request_mentions_ui_path": UI in first_text,
        "first_request_reported_line": next(
            (
                line
                for line in first_text.splitlines()
                if line.startswith("line=") and line != "line=unknown"
            ),
            None,
        ),
        "first_tool_calls": first_calls,
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
        "first_mutation_model_call": next(
            (
                item["model_call"]
                for item in sequence
                if item["name"] in {"apply_patch", "write_file"}
            ),
            None,
        ),
    }


def trace_agent_turns(trace: TaskTraceCollector) -> list[dict[str, Any]]:
    if trace.empty:
        return []
    return [
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
        for span in trace.batch().spans
        if getattr(span, "agent_role", None) is not None
        and getattr(span, "prompt_tokens", None) is not None
    ]


async def run(report_path: Path, *, label: str, source_sha: str) -> int:
    settings = Settings()
    raw: SiliconFlowDriver | None = None
    store = None
    report: dict[str, Any] = {
        "experiment": "P1.5.4-C Fixed Candidate Reviewer-Repair A/B",
        "variant": label,
        "source_sha": source_sha,
        "candidate_origin": "exact Developer candidate from first natural P1.5.4-C B run",
        "candidate_semantic_bug": "duplicate restart button: HTML owns restart-btn and JS appends another button",
        "model": settings.reviewer_model,
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
        with tempfile.TemporaryDirectory(prefix=f"p154c-fixed-{label.lower()}-") as temp:
            workspace = repository(Path(temp) / "repo")
            contract = task()
            run_id, project_id = uuid4(), uuid4()
            await _persist_acceptance_run(
                settings,
                run_id=run_id,
                project_id=project_id,
                task=contract,
                base_commit=workspace.head_commit(),
                repository_url=f"https://p154c-fixed.acceptance.devflow.local/{label}/{project_id}",
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
            scripted = ScriptedCandidateDriver()
            runner = _runner(
                settings,
                developer=_developer(
                    settings,
                    driver=scripted,
                    model="p154c/scripted-fixed-candidate",
                ),
                repair=_repair(settings, driver=recording),
                reviewer=_reviewer(settings, driver=recording),
            )
            result = await runner.run(contract, workspace=workspace, trace=trace)
            budget = await store.snapshot(run_id)
            report["scripted_developer_calls"] = scripted.calls
            report["outcome"] = _run_diagnostics(result, budget)
            report["reviews"] = [review.model_dump(mode="json") for review in result.reviews]
            report["repairs"] = [repair.model_dump(mode="json") for repair in result.repairs]
            report["model_records"] = recording.records
            report["repair_forensic"] = repair_forensic(recording.records)
            report["trace_agent_turns"] = trace_agent_turns(trace)
            report["token_audit"] = _budget_audit(budget)
            report["final_diff"] = workspace.unified_diff()[:30_000]
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
