from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.agents import DeveloperAgent, MultiTaskPlannerAgent, RepairAgent, ReviewerAgent
from app.context.token_estimator import TokenEstimator
from app.core.settings import Settings
from app.models.agent import (
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentRole,
    MessageRole,
    TokenUsage,
)
from app.models.checkpoint import CheckpointReason, CheckpointResumeStrategy
from app.models.dispatch import WorkerExecutionStatus
from app.models.run import TaskRunState
from app.models.task import TaskContract
from app.models.tools import ToolCall
from app.persistence import PostgresEvidenceStore
from app.persistence.token_budget import PostgresRunTokenBudgetStore
from app.providers.budgeted import BudgetedAgentDriver
from app.providers.siliconflow import SiliconFlowDriver
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.workers.executor import LocalQueuedTaskExecutionBackend
from app.workers.runtime import build_verifier
from app.workspace import LocalGitWorkspace


TARGET = "src/gomoku_engine.py"
VERIFY = (
    'python3 -c "from src.gomoku_engine import GameLogic; '
    'g = GameLogic(); assert g.is_valid_move(7, 7); '
    'assert not g.is_valid_move(-1, 7); assert not g.is_valid_move(15, 7)"'
)


def _git(root: Path, *args: str) -> str:
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
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / TARGET).write_text("BOARD_SIZE = 15\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "acceptance@devflow.local")
    _git(root, "config", "user.name", "DevFlow Acceptance")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "gomoku acceptance baseline")
    return LocalGitWorkspace(root)


def _manual_task() -> TaskContract:
    return TaskContract(
        task_id="gomoku-core",
        objective=(
            "在 src/gomoku_engine.py 中实现 GameLogic，并提供 "
            "is_valid_move(row, col) 判断 15x15 棋盘坐标是否合法。"
        ),
        readable_files=["src/**"],
        writable_files=[TARGET],
        readonly_files=["src/__init__.py"],
        acceptance_criteria=[
            "src.gomoku_engine.GameLogic 可以导入。",
            "GameLogic().is_valid_move(7, 7) 返回 True。",
            "负坐标和等于 15 的越界坐标返回 False。",
        ],
        verification_commands=[VERIFY],
        max_retries=2,
    )


def _planner_requirement() -> str:
    return (
        "只规划一个工作包，package_id 必须为 gomoku-core。"
        "只允许修改 src/gomoku_engine.py，不得修改 src/__init__.py。"
        "实现 GameLogic 类，棋盘大小为 15，并实现 is_valid_move(row, col)："
        "只有 0 <= row < 15 且 0 <= col < 15 时返回 True，否则返回 False。"
        "验证命令必须使用 python3 -c 从 src.gomoku_engine 导入 GameLogic，"
        "并断言 (7,7) 合法、(-1,7) 和 (15,7) 非法。"
        "不要创建额外文件或额外工作包。"
    )


class _NeverDriver:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        del request
        self.calls += 1
        raise AssertionError(f"{self.label} must not be called")


class _ScriptedDeveloperDriver:
    def __init__(self) -> None:
        patch = """*** Begin Patch
*** Update File: src/gomoku_engine.py
@@
 BOARD_SIZE = 15
+
+
+class GameLogic:
+    def __init__(self):
+        self.board_size = BOARD_SIZE
*** End Patch"""
        self._responses = iter(
            [
                AgentResponse(
                    model="acceptance/scripted-developer",
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="checkpoint-class-only",
                            name="apply_patch",
                            arguments=json.dumps({"patch": patch}),
                        )
                    ],
                    usage=TokenUsage(),
                    latency_ms=0,
                    finish_reason="tool_calls",
                ),
                AgentResponse(
                    model="acceptance/scripted-developer",
                    content="已修改文件: src/gomoku_engine.py",
                    tool_calls=[],
                    usage=TokenUsage(),
                    latency_ms=0,
                    finish_reason="stop",
                ),
            ]
        )

    async def complete(self, request):  # type: ignore[no-untyped-def]
        del request
        return next(self._responses)


class _Resolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self.workspace = workspace

    def resolve(self, project_id: UUID) -> LocalGitWorkspace:
        del project_id
        return self.workspace


class _NoopFence:
    @asynccontextmanager
    async def guard_task_git_mutation(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        yield object()


def _developer(
    settings: Settings,
    *,
    driver,
    model: str,
) -> DeveloperAgent:
    return DeveloperAgent(
        driver=driver,
        model=model,
        max_iterations=settings.developer_max_iterations,
        max_duration_seconds=settings.developer_max_duration_seconds,
        max_model_turn_seconds=settings.developer_max_model_turn_seconds,
        max_output_tokens=settings.developer_max_output_tokens,
        invalid_tool_retry_max_output_tokens=settings.developer_invalid_tool_retry_max_output_tokens,
        enable_thinking=False,
        context_compaction_enabled=True,
        role_context_projection_enabled=True,
        max_retained_tool_groups=settings.developer_max_retained_tool_groups,
        max_single_tool_result_tokens=settings.developer_max_single_tool_result_tokens,
        max_tool_results_per_turn_tokens=settings.developer_max_tool_results_per_turn_tokens,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_repo_map_enabled=True,
        runtime_event_condenser_enabled=True,
        runtime_stuck_detector_enabled=True,
        openhands_patch_enabled=True,
    )


def _repair(settings: Settings, *, driver) -> RepairAgent:
    return RepairAgent(
        driver=driver,
        model=settings.repair_model,
        max_iterations=settings.repair_max_iterations,
        max_duration_seconds=settings.repair_max_duration_seconds,
        max_model_turn_seconds=settings.repair_max_model_turn_seconds,
        max_output_tokens=settings.repair_max_output_tokens,
        enable_thinking=False,
        context_compaction_enabled=True,
        role_context_projection_enabled=True,
        max_single_tool_result_tokens=settings.repair_max_single_tool_result_tokens,
        max_tool_results_per_turn_tokens=settings.repair_max_tool_results_per_turn_tokens,
        max_read_range_lines=settings.repair_max_read_range_lines,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=True,
        runtime_event_condenser_enabled=True,
        runtime_stuck_detector_enabled=True,
        openhands_patch_enabled=True,
    )


def _reviewer(settings: Settings, *, driver) -> ReviewerAgent:
    return ReviewerAgent(
        driver=driver,
        model=settings.reviewer_model,
        max_output_tokens=settings.reviewer_max_output_tokens,
        enable_thinking=False,
        role_context_projection_enabled=True,
    )


def _runner(
    settings: Settings,
    *,
    developer: DeveloperAgent,
    repair: RepairAgent,
    reviewer: ReviewerAgent,
) -> SingleTaskOrchestrator:
    return SingleTaskOrchestrator(
        developer=developer,
        verifier=build_verifier(settings),
        reviewer=reviewer,
        repair=repair,
        developer_model=getattr(developer, "_model", "acceptance/developer"),
        reviewer_model=settings.reviewer_model,
        repair_model=settings.repair_model,
        minimum_repair_attempts=1,
    )


def _budgeted(
    *,
    raw_driver: SiliconFlowDriver,
    store: PostgresRunTokenBudgetStore,
    run_id: UUID,
    task_id: str,
    settings: Settings,
) -> BudgetedAgentDriver:
    return BudgetedAgentDriver(
        driver=raw_driver,
        budget_store=store,
        run_id=run_id,
        task_id=task_id,
        token_estimator=TokenEstimator(safety_factor=settings.token_estimate_safety_factor),
    )


async def _persist_acceptance_run(
    settings: Settings,
    *,
    run_id: UUID,
    project_id: UUID,
    task: TaskContract,
    base_commit: str,
    repository_url: str,
) -> None:
    if settings.database_url is None:
        raise RuntimeError("DEVFLOW_DATABASE_URL is required for acceptance")
    evidence = PostgresEvidenceStore.from_url(settings.database_url)
    try:
        persisted_project = await evidence.ensure_project(
            repository_url=repository_url,
            default_branch="main",
            project_id=project_id,
        )
        if persisted_project != project_id:
            raise AssertionError(
                f"acceptance project identity drifted: {persisted_project} != {project_id}"
            )
        await evidence.start_run(
            project_id=project_id,
            tasks=[task],
            base_commit=base_commit,
            run_id=run_id,
        )
    finally:
        await evidence.dispose()


async def _store(
    settings: Settings,
    *,
    run_id: UUID,
    total_tokens: int,
) -> PostgresRunTokenBudgetStore:
    if settings.database_url is None:
        raise RuntimeError("DEVFLOW_DATABASE_URL is required for acceptance")
    store = PostgresRunTokenBudgetStore.from_url(
        settings.database_url,
        default_total_budget_tokens=total_tokens,
        adaptive_package_budget_enabled=True,
        token_estimate_safety_factor=settings.token_estimate_safety_factor,
    )
    await store.initialize(run_id, total_budget_tokens=total_tokens)
    return store


async def _requirement_e2e(
    *,
    root: Path,
    settings: Settings,
    raw_driver: SiliconFlowDriver,
) -> dict[str, Any]:
    workspace = _repository(root)
    planner = MultiTaskPlannerAgent(
        driver=raw_driver,
        model=settings.planner_model,
        max_tasks=2,
        max_schema_repair_attempts=1,
        temperature=0.0,
        initial_max_output_tokens=settings.planner_initial_max_output_tokens,
        json_repair_max_output_tokens=settings.planner_json_repair_max_output_tokens,
        budget_replan_max_output_tokens=settings.planner_budget_replan_max_output_tokens,
        enable_thinking=False,
        adaptive_work_package_routing_enabled=True,
    )
    dag = await planner.plan(
        _planner_requirement(),
        repository_context=(
            "tracked_files:\n"
            "src/__init__.py\n"
            "src/gomoku_engine.py\n"
            "src/gomoku_engine.py currently contains only: BOARD_SIZE = 15"
        ),
    )
    if len(dag.tasks) != 1:
        raise AssertionError(f"real Planner produced {len(dag.tasks)} work packages")
    task = dag.tasks[0].task
    if task.task_id != "gomoku-core":
        raise AssertionError(f"Planner task_id drifted: {task.task_id}")
    if TARGET not in task.writable_files:
        raise AssertionError(f"Planner did not own {TARGET}: {task.writable_files}")

    run_id = uuid4()
    project_id = uuid4()
    await _persist_acceptance_run(
        settings,
        run_id=run_id,
        project_id=project_id,
        task=task,
        base_commit=workspace.head_commit(),
        repository_url=f"https://acceptance.devflow.local/{project_id}",
    )
    store = await _store(settings, run_id=run_id, total_tokens=50_000)
    budgeted = _budgeted(
        raw_driver=raw_driver,
        store=store,
        run_id=run_id,
        task_id=task.task_id,
        settings=settings,
    )
    try:
        runner = _runner(
            settings,
            developer=_developer(settings, driver=budgeted, model=settings.developer_model),
            repair=_repair(settings, driver=budgeted),
            reviewer=_reviewer(settings, driver=budgeted),
        )
        result = await runner.run(task, workspace=workspace)
        budget = await store.snapshot(run_id)
    finally:
        await store.dispose()

    if result.status is not TaskRunState.SUCCEEDED:
        raise AssertionError(
            "real requirement E2E failed: "
            + json.dumps(
                [failure.model_dump(mode="json") for failure in result.failures],
                ensure_ascii=False,
            )
        )
    module_text = (workspace.root / TARGET).read_text(encoding="utf-8")
    if "class GameLogic" not in module_text or "def is_valid_move" not in module_text:
        raise AssertionError("real E2E succeeded without the required GameLogic implementation")

    return {
        "status": result.status.value,
        "task_id": task.task_id,
        "planner_tokens": planner.last_usage.total_tokens,
        "repair_attempts": result.repair_attempts,
        "verification_attempts": len(result.verifications),
        "review_attempts": len(result.reviews),
        "run_budget_total": budget.total_budget_tokens,
        "run_budget_used": budget.used_total_tokens,
        "changed_files": result.changed_files,
    }


async def _real_import_repair(
    *,
    root: Path,
    settings: Settings,
    raw_driver: SiliconFlowDriver,
) -> dict[str, Any]:
    workspace = _repository(root)
    task = _manual_task()
    run_id = uuid4()
    project_id = uuid4()
    await _persist_acceptance_run(
        settings,
        run_id=run_id,
        project_id=project_id,
        task=task,
        base_commit=workspace.head_commit(),
        repository_url=f"https://acceptance.devflow.local/{project_id}",
    )
    store = await _store(settings, run_id=run_id, total_tokens=50_000)
    budgeted = _budgeted(
        raw_driver=raw_driver,
        store=store,
        run_id=run_id,
        task_id=task.task_id,
        settings=settings,
    )
    never = _NeverDriver("Developer during verifier-first ImportError acceptance")
    try:
        runner = _runner(
            settings,
            developer=_developer(settings, driver=never, model="acceptance/never-developer"),
            repair=_repair(settings, driver=budgeted),
            reviewer=_reviewer(settings, driver=budgeted),
        )
        result = await runner.run(
            task,
            workspace=workspace,
            resume_verification_first=True,
        )
        budget = await store.snapshot(run_id)
    finally:
        await store.dispose()

    if never.calls:
        raise AssertionError("verifier-first ImportError acceptance replayed Developer")
    if result.status is not TaskRunState.SUCCEEDED:
        raise AssertionError(
            "real ImportError repair failed: "
            + json.dumps(
                [failure.model_dump(mode="json") for failure in result.failures],
                ensure_ascii=False,
            )
        )
    if not result.repairs:
        raise AssertionError("real ImportError acceptance did not invoke Repair")
    first_stage = result.repairs[0].progress.failure_stage_before if result.repairs[0].progress else ""
    if "IMPORT_SYMBOL_MISSING" not in (first_stage or ""):
        raise AssertionError(f"expected IMPORT_SYMBOL_MISSING repair stage, got {first_stage!r}")

    return {
        "status": result.status.value,
        "repair_attempts": result.repair_attempts,
        "first_failure_stage": first_stage,
        "verification_attempts": len(result.verifications),
        "run_budget_total": budget.total_budget_tokens,
        "run_budget_used": budget.used_total_tokens,
    }


async def _checkpoint_resume(
    *,
    root: Path,
    settings: Settings,
    raw_driver: SiliconFlowDriver,
) -> dict[str, Any]:
    workspace = _repository(root)
    task = _manual_task()
    project_id = uuid4()
    resolver = _Resolver(workspace)
    fence = _NoopFence()
    base_commit = workspace.head_commit()

    first_run_id = uuid4()
    await _persist_acceptance_run(
        settings,
        run_id=first_run_id,
        project_id=project_id,
        task=task,
        base_commit=base_commit,
        repository_url=f"https://acceptance.devflow.local/{project_id}",
    )
    first_store = await _store(settings, run_id=first_run_id, total_tokens=1_000)
    first_budgeted = _budgeted(
        raw_driver=raw_driver,
        store=first_store,
        run_id=first_run_id,
        task_id=task.task_id,
        settings=settings,
    )
    first_runner = _runner(
        settings,
        developer=_developer(
            settings,
            driver=_ScriptedDeveloperDriver(),
            model="acceptance/scripted-developer",
        ),
        repair=_repair(settings, driver=first_budgeted),
        reviewer=_reviewer(settings, driver=first_budgeted),
    )
    first_backend = LocalQueuedTaskExecutionBackend(
        workspace_resolver=resolver,
        worktree_root=root.parent / "worktrees-first",
        runner_factory=lambda _task: first_runner,
        git_fence=fence,
    )
    try:
        first = await first_backend.execute(
            task=task,
            project_id=project_id,
            run_id=first_run_id,
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=base_commit,
        )
        first_budget = await first_store.snapshot(first_run_id)
    finally:
        await first_store.dispose()

    if first.status is not WorkerExecutionStatus.FAILED:
        raise AssertionError(f"forced-budget first run unexpectedly {first.status.value}")
    checkpoint = first.checkpoint
    if checkpoint is None:
        raise AssertionError("forced-budget first run did not create a checkpoint")
    if checkpoint.reason is not CheckpointReason.RUN_TOKEN_BUDGET_EXHAUSTED:
        raise AssertionError(f"unexpected checkpoint reason: {checkpoint.reason.value}")
    if checkpoint.resume_strategy is not CheckpointResumeStrategy.VERIFY_THEN_REPAIR:
        raise AssertionError(
            f"unexpected checkpoint strategy: {checkpoint.resume_strategy}"
        )
    if not first.run_result or not first.run_result.verifications:
        raise AssertionError("checkpoint was created before deterministic verification")
    if "is_valid_move" not in (
        workspace.root / TARGET
    ).read_text(encoding="utf-8"):
        # The base workspace must remain unchanged; the checkpoint commit lives in Git and is
        # intentionally not integrated into the project branch yet.
        pass

    second_run_id = uuid4()
    await _persist_acceptance_run(
        settings,
        run_id=second_run_id,
        project_id=project_id,
        task=task,
        base_commit=checkpoint.commit_sha,
        repository_url=f"https://acceptance.devflow.local/{project_id}",
    )
    second_store = await _store(settings, run_id=second_run_id, total_tokens=50_000)
    second_budgeted = _budgeted(
        raw_driver=raw_driver,
        store=second_store,
        run_id=second_run_id,
        task_id=task.task_id,
        settings=settings,
    )
    never = _NeverDriver("Developer during checkpoint VERIFY_THEN_REPAIR resume")
    second_runner = _runner(
        settings,
        developer=_developer(settings, driver=never, model="acceptance/never-developer"),
        repair=_repair(settings, driver=second_budgeted),
        reviewer=_reviewer(settings, driver=second_budgeted),
    )
    second_backend = LocalQueuedTaskExecutionBackend(
        workspace_resolver=resolver,
        worktree_root=root.parent / "worktrees-second",
        runner_factory=lambda _task: second_runner,
        git_fence=fence,
    )
    try:
        second = await second_backend.execute(
            task=task,
            project_id=project_id,
            run_id=second_run_id,
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=checkpoint.commit_sha,
            continuation_context=checkpoint.context_state,
            resume_verification_first=True,
        )
        second_budget = await second_store.snapshot(second_run_id)
    finally:
        await second_store.dispose()

    if never.calls:
        raise AssertionError("checkpoint resume replayed Developer instead of verifier-first")
    if second.status is not WorkerExecutionStatus.SUCCEEDED:
        failures = [failure.model_dump(mode="json") for failure in second.failures]
        raise AssertionError(
            "checkpoint resume failed: " + json.dumps(failures, ensure_ascii=False)
        )
    if second.run_result is None or second.run_result.status is not TaskRunState.SUCCEEDED:
        raise AssertionError("checkpoint resumed worker lacked a successful runtime result")
    if not second.run_result.events[0].detail.startswith("从验证失败检查点恢复"):
        raise AssertionError(
            f"resume did not start verifier-first: {second.run_result.events[0].detail}"
        )

    return {
        "first_run": {
            "status": first.status.value,
            "budget_total": first_budget.total_budget_tokens,
            "budget_used": first_budget.used_total_tokens,
            "checkpoint_reason": checkpoint.reason.value,
            "resume_strategy": checkpoint.resume_strategy.value,
            "checkpoint_commit": checkpoint.commit_sha,
            "verification_attempts": len(first.run_result.verifications),
        },
        "second_run": {
            "status": second.status.value,
            "budget_total": second_budget.total_budget_tokens,
            "budget_used": second_budget.used_total_tokens,
            "developer_calls": never.calls,
            "repair_attempts": second.run_result.repair_attempts,
            "verification_attempts": len(second.run_result.verifications),
            "base_commit": checkpoint.commit_sha,
        },
    }


async def _main(report_path: Path) -> int:
    settings = Settings()
    if settings.siliconflow_api_key is None:
        raise RuntimeError(
            "A Qwen/DashScope API key is required. Configure DASHSCOPE_API_KEY or the "
            "legacy SILICONFLOW_API_KEY alias before running this acceptance."
        )
    if settings.database_url is None:
        raise RuntimeError("DEVFLOW_DATABASE_URL is required")

    raw_driver = SiliconFlowDriver.from_settings(settings)
    report: dict[str, Any] = {
        "acceptance": "real-gomoku-runtime-v3",
        "models": {
            "planner": settings.planner_model,
            "developer": settings.developer_model,
            "repair": settings.repair_model,
            "reviewer": settings.reviewer_model,
        },
    }
    try:
        # Qwen/DashScope's OpenAI-compatible endpoint intentionally does not expose GET /models.
        # Validate the credential, endpoint and pinned model with one tiny real completion instead.
        probe = await raw_driver.complete(
            AgentRequest(
                role=AgentRole.PLANNER,
                model=settings.planner_model,
                messages=[
                    AgentMessage(
                        role=MessageRole.USER,
                        content="Reply with exactly: OK",
                    )
                ],
                temperature=0.0,
                max_output_tokens=64,
                enable_thinking=False,
            )
        )
        report["provider_probe"] = {
            "base_url": settings.siliconflow_base_url,
            "requested_model": settings.planner_model,
            "response_model": probe.model,
            "total_tokens": probe.usage.total_tokens,
        }

        with tempfile.TemporaryDirectory(prefix="devflow-real-gomoku-") as temp:
            root = Path(temp)
            report["requirement_e2e"] = await _requirement_e2e(
                root=root / "requirement",
                settings=settings,
                raw_driver=raw_driver,
            )
            report["import_repair"] = await _real_import_repair(
                root=root / "import-repair",
                settings=settings,
                raw_driver=raw_driver,
            )
            report["checkpoint_resume"] = await _checkpoint_resume(
                root=root / "checkpoint",
                settings=settings,
                raw_driver=raw_driver,
            )
    finally:
        await raw_driver.dispose()

    report["status"] = "PASS"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("real-gomoku-acceptance.json"),
    )
    args = parser.parse_args()
    return asyncio.run(_main(args.report))


if __name__ == "__main__":
    raise SystemExit(main())