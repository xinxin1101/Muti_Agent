from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.merge_queue import MergeQueueError, TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _task(task_id: str, writable: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Update {writable}.",
        readable_files=["**"],
        writable_files=[writable],
        readonly_files=[],
        acceptance_criteria=[f"{writable} is updated"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _run_result(task: models.TaskContract, changed_file: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=[changed_file],
    )


def _finish_worker(
    manager: workspace.TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: models.TaskContract,
    path: str,
    content: str,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path(path).write_text(content, encoding="utf-8")
    commit = manager.commit_task_changes(task.task_id)
    scheduler.start(task.task_id)
    scheduler.succeed(task.task_id)
    return models.WorkerTaskResult(
        task_id=task.task_id,
        scheduler_state=models.TaskScheduleState.SUCCEEDED,
        worktree_path=str(record.path),
        branch_name=record.branch_name,
        base_commit=record.base_commit,
        commit_sha=commit,
        run_result=_run_result(task, path),
        duration_ms=1,
    )


class TamperBeforeSecondIntegrationQueue(TopologicalMergeQueue):
    def __init__(self, *, repository_root: Path, **kwargs) -> None:
        self._repository_root = repository_root
        self._integration_calls = 0
        super().__init__(**kwargs)

    def _integrate_one(self, result: models.WorkerTaskResult) -> bool:
        self._integration_calls += 1
        if self._integration_calls == 2:
            _git(
                self._repository_root,
                "update-ref",
                f"refs/heads/{result.branch_name}",
                result.base_commit or "",
                result.commit_sha or "",
            )
        return super()._integrate_one(result)


def test_worker_branch_is_revalidated_immediately_before_each_integration(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "a.txt").write_text("A=base\n", encoding="utf-8")
    (root / "b.txt").write_text("B=base\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    base = workspace.LocalGitWorkspace(root)

    task_a = _task("TASK-A", "a.txt")
    task_b = _task("TASK-B", "b.txt")
    dag = models.TaskDAG(
        tasks=(
            models.TaskNode(task=task_a, depends_on=()),
            models.TaskNode(task=task_b, depends_on=()),
        )
    )
    scheduler = DAGScheduler(dag)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "a.txt", "A=task-a\n")
    result_b = _finish_worker(manager, scheduler, task_b, "b.txt", "B=task-b\n")
    queue = TamperBeforeSecondIntegrationQueue(
        repository_root=base.root,
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id="run-001",
    )

    with pytest.raises(MergeQueueError, match="branch moved after worker finalization"):
        queue.integrate([result_a, result_b])

    snapshot = queue.snapshot()
    assert snapshot.integrated_task_ids == ("TASK-A",)
    assert len(snapshot.attempts) == 1
    assert snapshot.attempts[0].task_id == "TASK-A"
    assert _git(base.root, "rev-parse", queue.integration_ref) == snapshot.head_commit
    assert base.changed_files() == []
