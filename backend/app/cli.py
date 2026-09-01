from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.core.settings import Settings, get_settings
from app.models import DockerSandboxPolicy, TaskContract, TaskRunState
from app.providers.siliconflow import SiliconFlowDriver
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.verification import DeterministicVerifier, DockerSandboxRunner
from app.workspace import LocalGitWorkspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devflow",
        description="DevFlow evidence-driven single-task runtime.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run one validated TaskContract against a local Git workspace.",
    )
    run_parser.add_argument(
        "--workspace",
        required=True,
        type=Path,
        help="Path to the managed local Git repository top-level directory.",
    )
    run_parser.add_argument(
        "--task",
        required=True,
        type=Path,
        help="Path to a JSON file containing one validated TaskContract payload.",
    )
    return parser


def load_task(path: Path) -> TaskContract:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return TaskContract.model_validate(payload)


def build_verifier(settings: Settings) -> DeterministicVerifier:
    policy = DockerSandboxPolicy(
        image=settings.verification_sandbox_image,
        cpus=settings.verification_sandbox_cpus,
        memory_mb=settings.verification_sandbox_memory_mb,
        pids_limit=settings.verification_sandbox_pids_limit,
        tmpfs_mb=settings.verification_sandbox_tmpfs_mb,
        shm_mb=settings.verification_sandbox_shm_mb,
    )
    return DeterministicVerifier(
        command_timeout_seconds=settings.verification_sandbox_timeout_seconds,
        command_runner=DockerSandboxRunner(policy),
    )


async def run_single_task(*, workspace_path: Path, task_path: Path):
    task = load_task(task_path)
    workspace = LocalGitWorkspace(workspace_path)
    settings = get_settings()
    driver = SiliconFlowDriver.from_settings(settings)

    developer = DeveloperAgent(
        driver=driver,
        model=settings.developer_model,
        max_iterations=settings.developer_max_iterations,
        max_duration_seconds=settings.developer_max_duration_seconds,
        max_model_turn_seconds=settings.developer_max_model_turn_seconds,
        max_output_tokens=settings.developer_max_output_tokens,
        invalid_tool_retry_max_output_tokens=settings.developer_invalid_tool_retry_max_output_tokens,
        enable_thinking=settings.developer_enable_thinking,
    )
    reviewer = ReviewerAgent(
        driver=driver,
        model=settings.reviewer_model,
        max_output_tokens=settings.reviewer_max_output_tokens,
        enable_thinking=settings.reviewer_enable_thinking,
    )
    repair = RepairAgent(
        driver=driver,
        model=settings.repair_model,
        max_iterations=settings.repair_max_iterations,
        max_duration_seconds=settings.repair_max_duration_seconds,
        max_model_turn_seconds=settings.repair_max_model_turn_seconds,
        max_output_tokens=settings.repair_max_output_tokens,
        enable_thinking=settings.repair_enable_thinking,
    )
    verifier = build_verifier(settings)
    orchestrator = SingleTaskOrchestrator(
        developer=developer,
        verifier=verifier,
        reviewer=reviewer,
        repair=repair,
        developer_model=settings.developer_model,
        reviewer_model=settings.reviewer_model,
        repair_model=settings.repair_model,
        minimum_repair_attempts=settings.minimum_repair_attempts,
    )
    return await orchestrator.run(task, workspace=workspace)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command != "run":
            parser.error(f"unsupported command: {args.command}")
        result = asyncio.run(
            run_single_task(
                workspace_path=args.workspace,
                task_path=args.task,
            )
        )
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    print(result.model_dump_json(indent=2))
    return 0 if result.status is TaskRunState.SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
