from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.scheduler import DAGScheduler
from app.runtime.worker_coordinator import ParallelWorkerCoordinator


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def _task(task_id: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Complete {task_id}.",
        readable_files=["**"],
        writable_files=["shared.txt"],
        readonly_files=[],
        acceptance_criteria=["shared.txt is updated"],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _success(task: models.TaskContract) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=["shared.txt"],
    )


class GateRunner:
    def __init__(self, *, reached: asyncio.Event, release: asyncio.Event) -> None:
        self._reached = reached
        self._release = release

    async def run(
        self,
        task: models.TaskContract,
        *,
        workspace: workspace.LocalGitWorkspace,
    ) -> SingleTaskRunResult:
        workspace.resolve_path("shared.txt").write_text(task.task_id + "\n", encoding="utf-8")
        self._reached.set()
        await self._release.wait()
        return _success(task)


class ImmediateRunner:
    async def run(
        self,
        task: models.TaskContract,
        *,
        workspace: workspace.LocalGitWorkspace,
    ) -> SingleTaskRunResult:
        workspace.resolve_path("shared.txt").write_text(task.task_id + "\n", encoding="utf-8")
        return _success(task)


def test_same_coordinator_rejects_overlapping_ready_wave_calls(tmp_path: Path) -> None:
    async def scenario() -> None:
        base = _repository(tmp_path)
        task = _task("TASK-A")
        scheduler = DAGScheduler(
            models.TaskDAG(tasks=(models.TaskNode(task=task, depends_on=()),))
        )
        manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
        reached = asyncio.Event()
        release = asyncio.Event()
        coordinator = ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: GateRunner(reached=reached, release=release),
            max_concurrency=1,
        )

        first_wave = asyncio.create_task(coordinator.run_ready_wave())
        await asyncio.wait_for(reached.wait(), timeout=3)

        with pytest.raises(RuntimeError, match="already has an active READY wave"):
            await coordinator.run_ready_wave()

        release.set()
        result = await asyncio.wait_for(first_wave, timeout=5)
        assert result.task_results[0].scheduler_state is models.TaskScheduleState.SUCCEEDED

    asyncio.run(scenario())


def test_dependent_next_wave_requires_dependency_integrated_base_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        base = _repository(tmp_path)
        task_a = _task("TASK-A")
        task_b = _task("TASK-B")
        scheduler = DAGScheduler(
            models.TaskDAG(
                tasks=(
                    models.TaskNode(task=task_a, depends_on=()),
                    models.TaskNode(task=task_b, depends_on=("TASK-A",)),
                )
            )
        )
        manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
        coordinator = ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ImmediateRunner(),
        )

        first_wave = await coordinator.run_ready_wave()
        assert first_wave.next_ready_task_ids == ("TASK-B",)
        assert scheduler.state("TASK-B") is models.TaskScheduleState.READY

        with pytest.raises(RuntimeError, match="integrated descendant base commit"):
            await coordinator.run_ready_wave()

        assert scheduler.state("TASK-B") is models.TaskScheduleState.READY
        assert not manager.record_for("TASK-B").path.exists()

    asyncio.run(scenario())


def test_external_descendant_base_can_enable_a_later_dependent_wave(tmp_path: Path) -> None:
    async def scenario() -> None:
        base = _repository(tmp_path)
        task_a = _task("TASK-A")
        task_b = _task("TASK-B")
        scheduler = DAGScheduler(
            models.TaskDAG(
                tasks=(
                    models.TaskNode(task=task_a, depends_on=()),
                    models.TaskNode(task=task_b, depends_on=("TASK-A",)),
                )
            )
        )
        manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
        first = ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ImmediateRunner(),
        )

        first_wave = await first.run_ready_wave()
        assert first_wave.next_ready_task_ids == ("TASK-B",)

        (base.root / "shared.txt").write_text("integrated-task-a\n", encoding="utf-8")
        _git(base.root, "add", "shared.txt")
        _git(base.root, "commit", "-m", "synthetic dependency integration")
        integrated_commit = _git(base.root, "rev-parse", "HEAD")

        second = ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ImmediateRunner(),
            task_base_resolver=lambda task_id: (
                integrated_commit if task_id == "TASK-B" else None
            ),
        )
        second_wave = await second.run_ready_wave()

        assert second_wave.scheduled_task_ids == ("TASK-B",)
        assert second_wave.task_results[0].base_commit == integrated_commit
        assert second_wave.task_results[0].scheduler_state is models.TaskScheduleState.SUCCEEDED
        assert scheduler.is_terminal

    asyncio.run(scenario())
