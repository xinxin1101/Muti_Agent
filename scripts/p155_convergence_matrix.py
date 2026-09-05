from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, "/tmp")
sys.path.insert(0, str(Path.cwd() / "scripts"))

from p154c_fixed_candidate_repair_ab import (  # noqa: E402
    FIXED_INDEX,
    FIXED_UI,
    INDEX,
    RUN_BUDGET,
    UI,
    RecordingDriver,
    _budget_audit,
    _budgeted,
    _developer,
    _persist_acceptance_run,
    _repair,
    _reviewer,
    _run_diagnostics,
    _runner,
    _store,
    git,
    repository,
    task,
    trace_agent_turns,
)
from app.core.settings import Settings  # noqa: E402
from app.models.agent import AgentResponse, TokenUsage  # noqa: E402
from app.models.tools import ToolCall  # noqa: E402
from app.providers.siliconflow import SiliconFlowDriver  # noqa: E402
from app.trace.collector import TaskTraceCollector  # noqa: E402

RUNTIME_SHA = "97021ca208dedcd787ac90e1a77aa186aeb06848"

_DYNAMIC_RESTART = """    const resetBtn = document.createElement('button');
    resetBtn.textContent = '重新开始';
    resetBtn.onclick = resetGame;
    document.getElementById('app').appendChild(resetBtn);
"""
_BOUND_RESTART = """    const resetBtn = document.getElementById('restart-btn');
    if (resetBtn) {
      resetBtn.onclick = resetGame;
    }
"""
_RESET_BODY = """  function resetGame() {
    initBoard();
    const canvas = document.querySelector('#gomoku-board canvas');
    if (canvas) {
      const ctx = canvas.getContext('2d');
      drawBoard(ctx);
    }
  }
"""


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"expected exactly one source match for {old[:80]!r}")
    return text.replace(old, new)


CLEAN_INDEX = FIXED_INDEX
CLEAN_UI = replace_once(FIXED_UI, _DYNAMIC_RESTART, _BOUND_RESTART)


@dataclass(frozen=True)
class Scenario:
    case_id: str
    description: str
    target_path: str
    candidate_index: str
    candidate_ui: str
    repaired_index: str
    repaired_ui: str
    clean_index: str
    clean_ui: str
    expectations: tuple[dict[str, Any], ...]

    def target_content(self, *, clean: bool) -> str:
        if self.target_path == INDEX:
            return self.clean_index if clean else self.repaired_index
        if self.target_path == UI:
            return self.clean_ui if clean else self.repaired_ui
        raise ValueError(f"unsupported target path: {self.target_path}")

    def fingerprints(self) -> dict[str, str]:
        def digest(value: str) -> str:
            return hashlib.sha256(value.encode("utf-8")).hexdigest()

        return {
            "candidate_index_sha256": digest(self.candidate_index),
            "candidate_ui_sha256": digest(self.candidate_ui),
            "repaired_index_sha256": digest(self.repaired_index),
            "repaired_ui_sha256": digest(self.repaired_ui),
            "clean_index_sha256": digest(self.clean_index),
            "clean_ui_sha256": digest(self.clean_ui),
        }


def expectation(
    expectation_id: str,
    kind: str,
    *,
    file: str,
    patterns: tuple[str, ...],
    minimum_pattern_matches: int = 1,
) -> dict[str, Any]:
    return {
        "expectation_id": expectation_id,
        "kind": kind,
        "file": file,
        "message_patterns": list(patterns),
        "minimum_pattern_matches": minimum_pattern_matches,
    }


def scenarios() -> dict[str, Scenario]:
    inline_style_ui = replace_once(
        CLEAN_UI,
        _BOUND_RESTART,
        """    const resetBtn = document.getElementById('restart-btn');
    if (resetBtn) {
      resetBtn.onclick = resetGame;
      resetBtn.style.marginTop = '8px';
    }
""",
    )
    unbound_ui = replace_once(
        CLEAN_UI,
        _BOUND_RESTART,
        """    const resetBtn = document.getElementById('restart-btn');
    if (resetBtn) {
      resetBtn.dataset.ready = 'true';
    }
""",
    )
    console_ui = replace_once(
        CLEAN_UI,
        _BOUND_RESTART,
        """    const resetBtn = document.getElementById('restart-btn');
    if (resetBtn) {
      resetBtn.onclick = resetGame;
      console.log('restart control ready');
    }
""",
    )
    reset_no_redraw_ui = replace_once(
        CLEAN_UI,
        _RESET_BODY,
        """  function resetGame() {
    initBoard();
  }
""",
    )
    dead_helper_ui = replace_once(
        CLEAN_UI,
        "  document.addEventListener('DOMContentLoaded', function() {\n",
        """  function getResetButton() {
    return document.getElementById('restart-btn');
  }

  document.addEventListener('DOMContentLoaded', function() {
""",
    )
    no_occupied_guard_ui = replace_once(
        CLEAN_UI,
        "    if (board[row][col] !== 0) return;\n\n",
        "",
    )
    vague_guard_ui = replace_once(
        CLEAN_UI,
        "    if (board[row][col] !== 0) return;\n",
        """    const x = board[row][col];
    if (x !== 0) return;
""",
    )
    no_alternation_ui = replace_once(
        CLEAN_UI,
        "    currentPlayer = currentPlayer === 1 ? 2 : 1;\n",
        "    currentPlayer = 1;\n",
    )
    map_toggle_ui = replace_once(
        CLEAN_UI,
        "    currentPlayer = currentPlayer === 1 ? 2 : 1;\n",
        """    const nextPlayerByCurrent = { 1: 2, 2: 1 };
    currentPlayer = nextPlayerByCurrent[currentPlayer];
""",
    )
    wrong_size_ui = replace_once(CLEAN_UI, "  const BOARD_SIZE = 15;\n", "  const BOARD_SIZE = 14;\n")
    hardcoded_canvas_ui = replace_once(
        CLEAN_UI,
        """    canvas.width = BOARD_SIZE * CELL_SIZE + PADDING * 2;
    canvas.height = BOARD_SIZE * CELL_SIZE + PADDING * 2;
""",
        """    canvas.width = 490;
    canvas.height = 490;
""",
    )
    external_index = replace_once(
        CLEAN_INDEX,
        '  <script src="gomoku_ui.js"></script>\n',
        """  <script src="https://cdn.example.invalid/gomoku-helper.js"></script>
  <script src="gomoku_ui.js"></script>
""",
    )
    styled_button_index = replace_once(
        CLEAN_INDEX,
        '    <button id="restart-btn">重新开始</button>\n',
        '    <button id="restart-btn" style="margin-top: 8px">重新开始</button>\n',
    )
    missing_button_index = replace_once(
        CLEAN_INDEX,
        '    <button id="restart-btn">重新开始</button>\n',
        "",
    )
    duplicate_class_index = replace_once(
        CLEAN_INDEX,
        '    <button id="restart-btn">重新开始</button>\n',
        '    <button id="restart-btn" class="restart-control restart-control">重新开始</button>\n',
    )

    cases = (
        Scenario(
            case_id="duplicate-restart",
            description="HTML already owns restart control while JS appends a second restart button.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=FIXED_UI,
            repaired_index=CLEAN_INDEX,
            repaired_ui=inline_style_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-duplicate-restart",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"restart|重新开始", r"duplicate|second|two|重复|两个"),
                    minimum_pattern_matches=2,
                ),
                expectation(
                    "trap-inline-style",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"inline style|style\.marginTop|styling|样式",),
                ),
            ),
        ),
        Scenario(
            case_id="restart-unbound",
            description="The existing restart button is never bound to resetGame.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=unbound_ui,
            repaired_index=CLEAN_INDEX,
            repaired_ui=console_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-restart-unbound",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"restart|reset|重新开始", r"handler|onclick|bound|绑定|工作"),
                    minimum_pattern_matches=2,
                ),
                expectation(
                    "trap-console-log",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"console\.log|debug log|debugging|调试",),
                ),
            ),
        ),
        Scenario(
            case_id="reset-no-redraw",
            description="Reset clears state but leaves already drawn pieces visible on the canvas.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=reset_no_redraw_ui,
            repaired_index=CLEAN_INDEX,
            repaired_ui=dead_helper_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-reset-redraw",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"reset|restart|重置|重新开始", r"redraw|canvas|visual|clear|画布|棋盘"),
                    minimum_pattern_matches=2,
                ),
                expectation(
                    "trap-dead-helper",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"getResetButton|unused|dead code|未使用",),
                ),
            ),
        ),
        Scenario(
            case_id="occupied-overwrite",
            description="Clicking an occupied intersection can overwrite the existing piece.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=no_occupied_guard_ui,
            repaired_index=CLEAN_INDEX,
            repaired_ui=vague_guard_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-occupied-overwrite",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"occupied|overwrite|same position|重复落子|已有位置",),
                ),
                expectation(
                    "trap-vague-name",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"variable.*\bx\b|name.*\bx\b|naming|命名",),
                ),
            ),
        ),
        Scenario(
            case_id="no-player-alternation",
            description="Every legal move forces currentPlayer back to black instead of alternating.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=no_alternation_ui,
            repaired_index=CLEAN_INDEX,
            repaired_ui=map_toggle_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-player-alternation",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"alternate|alternating|switch player|always black|轮流|切换",),
                ),
                expectation(
                    "trap-toggle-complexity",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"nextPlayerByCurrent|object.*alloc|allocation|overcomplic|performance|复杂",),
                ),
            ),
        ),
        Scenario(
            case_id="wrong-board-size",
            description="The implementation renders a 14x14 board instead of the required 15x15 board.",
            target_path=UI,
            candidate_index=CLEAN_INDEX,
            candidate_ui=wrong_size_ui,
            repaired_index=CLEAN_INDEX,
            repaired_ui=hardcoded_canvas_ui,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-board-size",
                    "PRIMARY_BLOCKER",
                    file=UI,
                    patterns=(r"14|15x15|15 x 15|board size|棋盘",),
                ),
                expectation(
                    "trap-hardcoded-canvas",
                    "CHURN_TRAP",
                    file=UI,
                    patterns=(r"490|hard.?coded|magic number|maintainability|硬编码",),
                ),
            ),
        ),
        Scenario(
            case_id="external-resource",
            description="The page depends on an external CDN script despite the offline requirement.",
            target_path=INDEX,
            candidate_index=external_index,
            candidate_ui=CLEAN_UI,
            repaired_index=styled_button_index,
            repaired_ui=CLEAN_UI,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-external-resource",
                    "PRIMARY_BLOCKER",
                    file=INDEX,
                    patterns=(r"external|third.?party|cdn|network|外部|第三方",),
                ),
                expectation(
                    "trap-inline-html-style",
                    "CHURN_TRAP",
                    file=INDEX,
                    patterns=(r"inline style|style=|styling|样式",),
                ),
            ),
        ),
        Scenario(
            case_id="missing-restart-control",
            description="The HTML omits the required restart button while JS only looks it up by id.",
            target_path=INDEX,
            candidate_index=missing_button_index,
            candidate_ui=CLEAN_UI,
            repaired_index=duplicate_class_index,
            repaired_ui=CLEAN_UI,
            clean_index=CLEAN_INDEX,
            clean_ui=CLEAN_UI,
            expectations=(
                expectation(
                    "primary-missing-restart",
                    "PRIMARY_BLOCKER",
                    file=INDEX,
                    patterns=(r"restart|reset|重新开始", r"missing|absent|no button|缺少|不存在"),
                    minimum_pattern_matches=2,
                ),
                expectation(
                    "trap-duplicate-class",
                    "CHURN_TRAP",
                    file=INDEX,
                    patterns=(r"duplicate class|restart-control.*duplicate|duplicate.*restart-control|重复.*class",),
                ),
            ),
        ),
    )
    return {case.case_id: case for case in cases}


class ScriptedCandidateDriver:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.calls = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        del request
        self.calls += 1
        if self.calls == 1:
            return AgentResponse(
                model="p155/scripted-candidate",
                content="",
                tool_calls=[
                    ToolCall(
                        id=f"candidate-index-{self.scenario.case_id}",
                        name="write_file",
                        arguments=json.dumps(
                            {"path": INDEX, "content": self.scenario.candidate_index},
                            ensure_ascii=False,
                        ),
                    ),
                    ToolCall(
                        id=f"candidate-ui-{self.scenario.case_id}",
                        name="write_file",
                        arguments=json.dumps(
                            {"path": UI, "content": self.scenario.candidate_ui},
                            ensure_ascii=False,
                        ),
                    ),
                ],
                usage=TokenUsage(),
                latency_ms=0,
                finish_reason="tool_calls",
            )
        return AgentResponse(
            model="p155/scripted-candidate",
            content=f"已修改文件: {INDEX}, {UI}",
            tool_calls=[],
            usage=TokenUsage(),
            latency_ms=0,
            finish_reason="stop",
        )


class ScriptedRepairDriver:
    """Deterministically repair the primary blocker, then remove the pre-registered trap."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.calls = 0
        self.mutations = 0
        self.records: list[dict[str, Any]] = []

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls % 2 == 1:
            call = ToolCall(
                id=f"repair-read-{self.calls}-{self.scenario.case_id}",
                name="read_range",
                arguments=json.dumps(
                    {"path": self.scenario.target_path, "start_line": 1, "end_line": 220}
                ),
            )
            action = "observe"
        else:
            self.mutations += 1
            content = self.scenario.target_content(clean=self.mutations >= 2)
            call = ToolCall(
                id=f"repair-write-{self.calls}-{self.scenario.case_id}",
                name="write_file",
                arguments=json.dumps(
                    {"path": self.scenario.target_path, "content": content},
                    ensure_ascii=False,
                ),
            )
            action = "clean_trap" if self.mutations >= 2 else "repair_with_trap"

        self.records.append(
            {
                "call": self.calls,
                "action": action,
                "execution_iteration": request.execution_iteration,
                "closure_prompt_visible": any(
                    "ReviewerClosureContext" in message.content for message in request.messages
                ),
            }
        )
        return AgentResponse(
            model="p155/scripted-repair",
            content="",
            tool_calls=[call],
            usage=TokenUsage(),
            latency_ms=0,
            finish_reason="tool_calls",
        )


class ClosureToggleReviewer:
    """Keep one frozen Runtime and vary only whether closure evidence reaches Reviewer."""

    def __init__(self, inner, *, enabled: bool) -> None:
        self.inner = inner
        self.enabled = enabled
        self.rounds: list[dict[str, Any]] = []

    async def review(
        self,
        task_contract,
        verification,
        *,
        workspace,
        context_packet=None,
        closure_context=None,
        trace=None,
    ):
        self.rounds.append(
            {
                "round": len(self.rounds) + 1,
                "runtime_supplied_closure": closure_context is not None,
                "forwarded_closure": self.enabled and closure_context is not None,
            }
        )
        return await self.inner.review(
            task_contract,
            verification,
            workspace=workspace,
            context_packet=context_packet,
            closure_context=closure_context if self.enabled else None,
            trace=trace,
        )

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


def reviewer_tokens(records: list[dict[str, Any]]) -> dict[str, int]:
    prompt = 0
    completion = 0
    total = 0
    for record in records:
        usage = record.get("usage", {})
        prompt += int(usage.get("prompt_tokens") or 0)
        completion += int(usage.get("completion_tokens") or 0)
        total += int(usage.get("total_tokens") or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "model_calls": len(records),
    }


async def run(report_path: Path, *, label: str, case_id: str, source_sha: str) -> int:
    case_map = scenarios()
    scenario = case_map[case_id]
    settings = Settings()
    raw: SiliconFlowDriver | None = None
    store = None
    closure_enabled = label == "B"
    report: dict[str, Any] = {
        "experiment": "P1.5.5 Tier1 Controlled Closure Isolation Matrix",
        "variant": label,
        "case_id": case_id,
        "source_sha": source_sha,
        "runtime_sha_expected": RUNTIME_SHA,
        "closure_enabled": closure_enabled,
        "repair_mode": "deterministic scripted repair; real qwen reviewer",
        "description": scenario.description,
        "target_path": scenario.target_path,
        "expectations": list(scenario.expectations),
        "fingerprints": scenario.fingerprints(),
        "model": settings.reviewer_model,
        "temperature": 0.0,
        "run_budget_tokens": RUN_BUDGET,
    }
    try:
        if settings.siliconflow_api_key is None or settings.database_url is None:
            raise RuntimeError("provider/database configuration missing")
        actual_sha = git(Path.cwd(), "rev-parse", "HEAD")
        report["actual_checkout_sha"] = actual_sha
        if actual_sha != source_sha or source_sha != RUNTIME_SHA:
            raise RuntimeError(
                f"checkout drift: expected frozen runtime {RUNTIME_SHA}, got {actual_sha}"
            )

        raw = SiliconFlowDriver.from_settings(settings)
        with tempfile.TemporaryDirectory(prefix=f"p155-{case_id}-{label.lower()}-") as temp:
            workspace = repository(Path(temp) / "repo")
            contract = task()
            run_id, project_id = uuid4(), uuid4()
            await _persist_acceptance_run(
                settings,
                run_id=run_id,
                project_id=project_id,
                task=contract,
                base_commit=workspace.head_commit(),
                repository_url=f"https://p155.acceptance.devflow.local/{case_id}/{label}/{project_id}",
            )
            store = await _store(settings, run_id=run_id, total_tokens=RUN_BUDGET)
            budgeted = _budgeted(
                raw_driver=raw,
                store=store,
                run_id=run_id,
                task_id=contract.task_id,
                settings=settings,
            )
            recording_reviewer_driver = RecordingDriver(budgeted)
            reviewer = ClosureToggleReviewer(
                _reviewer(settings, driver=recording_reviewer_driver),
                enabled=closure_enabled,
            )
            scripted_candidate = ScriptedCandidateDriver(scenario)
            scripted_repair = ScriptedRepairDriver(scenario)
            trace = TaskTraceCollector(
                run_id=run_id,
                task_id=contract.task_id,
                dispatch_id=uuid4(),
                generation=1,
            )
            runner = _runner(
                settings,
                developer=_developer(
                    settings,
                    driver=scripted_candidate,
                    model="p155/scripted-candidate",
                ),
                repair=_repair(settings, driver=scripted_repair),
                reviewer=reviewer,
            )
            result = await runner.run(contract, workspace=workspace, trace=trace)
            budget = await store.snapshot(run_id)

            report["scripted_developer_calls"] = scripted_candidate.calls
            report["scripted_repair_calls"] = scripted_repair.calls
            report["scripted_repair_mutations"] = scripted_repair.mutations
            report["scripted_repair_records"] = scripted_repair.records
            report["closure_rounds"] = reviewer.rounds
            report["outcome"] = _run_diagnostics(result, budget)
            report["reviews"] = [review.model_dump(mode="json") for review in result.reviews]
            report["repairs"] = [repair.model_dump(mode="json") for repair in result.repairs]
            report["reviewer_model_records"] = recording_reviewer_driver.records
            report["reviewer_token_audit"] = reviewer_tokens(recording_reviewer_driver.records)
            report["trace_agent_turns"] = trace_agent_turns(trace)
            report["run_budget_audit"] = _budget_audit(budget)
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
    parser.add_argument("--report", type=Path)
    parser.add_argument("--label", choices=("A", "B"))
    parser.add_argument("--case-id", choices=tuple(scenarios()))
    parser.add_argument("--source-sha", default=RUNTIME_SHA)
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()

    if args.list_cases:
        payload = {
            case_id: {
                "description": scenario.description,
                "target_path": scenario.target_path,
                "expectations": list(scenario.expectations),
                "fingerprints": scenario.fingerprints(),
            }
            for case_id, scenario in scenarios().items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.report is None or args.label is None or args.case_id is None:
        parser.error("--report, --label and --case-id are required unless --list-cases is used")
    return asyncio.run(
        run(
            args.report,
            label=args.label,
            case_id=args.case_id,
            source_sha=args.source_sha,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
