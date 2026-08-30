from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.merge_queue import MergeQueueError, TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _git_code(root: Path, *arguments: str) -> int:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def _repository(tmp_path: Path) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "a.txt").write_text("A=base\n", encoding="utf-8")
    (root / "b.txt").write_text("B=base\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def _task(task_id: str, writable: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Update {writable} for {task_id}.",
        readable_files=["**"],
        writable_files=[writable],
        readonly_files=[],
        acceptance_criteria=[f"{writable} contains the {task_id} result"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _dag(*nodes: tuple[models.TaskContract, tuple[str, ...]]) -> models.TaskDAG:
    return models.TaskDAG(
        tasks=tuple(models.TaskNode(task=task, depends_on=depends_on) for task, depends_on in nodes)
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
    repository_path: str,
    content: str,
    *,
    base_commit: str | None = None,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id, base_commit=base_commit)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path(repository_path).write_text(content, encoding="utf-8")
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
        run_result=_run_result(task, repository_path),
        duration_ms=1,
    )


def _queue(
    scheduler: DAGScheduler,
    manager: workspace.TaskWorktreeManager,
    base: workspace.LocalGitWorkspace,
    *,
    integration_id: str = "run-001",
) -> TopologicalMergeQueue:
    return TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id=integration_id,
    )


def test_reverse_input_integrates_in_deterministic_dag_order(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    base_head = _git(base.root, "rev-parse", "HEAD")
    task_a = _task("TASK-A", "a.txt")
    task_b = _task("TASK-B", "b.txt")
    scheduler = DAGScheduler(_dag((task_a, ()), (task_b, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "a.txt", "A=task-a\n")
    result_b = _finish_worker(manager, scheduler, task_b, "b.txt", "B=task-b\n")
    queue = _queue(scheduler, manager, base)

    snapshot = queue.integrate([result_b, result_a])

    assert snapshot.integrated_task_ids == ("TASK-A", "TASK-B")
    assert [attempt.task_id for attempt in snapshot.attempts] == ["TASK-A", "TASK-B"]
    assert _git(base.root, "rev-parse", queue.integration_ref) == snapshot.head_commit
    assert _git(base.root, "rev-parse", "HEAD") == base_head
    assert base.changed_files() == []
    assert (
        _git_code(
            base.root,
            "merge-base",
            "--is-ancestor",
            result_a.commit_sha or "",
            snapshot.head_commit,
        )
        == 0
    )
    assert (
        _git_code(
            base.root,
            "merge-base",
            "--is-ancestor",
            result_b.commit_sha or "",
            snapshot.head_commit,
        )
        == 0
    )


def test_higher_topological_success_waits_for_earlier_success(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A", "a.txt")
    task_b = _task("TASK-B", "b.txt")
    scheduler = DAGScheduler(_dag((task_a, ()), (task_b, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "a.txt", "A=task-a\n")
    result_b = _finish_worker(manager, scheduler, task_b, "b.txt", "B=task-b\n")
    queue = _queue(scheduler, manager, base)

    with pytest.raises(MergeQueueError, match="earlier topological task TASK-A"):
        queue.integrate([result_b])

    assert queue.integrated_task_ids == ()
    assert queue.head_commit == manager.base_commit
    queue.integrate([result_a, result_b])
    assert queue.integrated_task_ids == ("TASK-A", "TASK-B")


def test_dependent_task_receives_trusted_integration_base(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A", "a.txt")
    task_c = _task("TASK-C", "b.txt")
    scheduler = DAGScheduler(_dag((task_a, ()), (task_c, ("TASK-A",))))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "a.txt", "A=task-a\n")
    queue = _queue(scheduler, manager, base)

    first = queue.integrate([result_a])
    trusted_base = queue.base_commit_for("TASK-C")

    assert scheduler.state("TASK-C") is models.TaskScheduleState.READY
    assert trusted_base == first.head_commit

    result_c = _finish_worker(
        manager,
        scheduler,
        task_c,
        "b.txt",
        "B=task-c\n",
        base_commit=trusted_base,
    )
    final = queue.integrate([result_c])

    assert final.integrated_task_ids == ("TASK-A", "TASK-C")
    assert result_c.base_commit == trusted_base
    assert final.head_commit != trusted_base
    assert base.changed_files() == []


def test_existing_integration_ref_recovers_successful_history(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A", "a.txt")
    task_b = _task("TASK-B", "b.txt")
    scheduler = DAGScheduler(_dag((task_a, ()), (task_b, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "a.txt", "A=task-a\n")
    result_b = _finish_worker(manager, scheduler, task_b, "b.txt", "B=task-b\n")
    original = _queue(
        scheduler,
        manager,
        base,
        integration_id="recoverable-run",
    )
    original_snapshot = original.integrate([result_a, result_b])

    recovered = _queue(
        scheduler,
        manager,
        base,
        integration_id="recoverable-run",
    )
    recovered_snapshot = recovered.snapshot()

    assert recovered_snapshot.head_commit == original_snapshot.head_commit
    assert recovered_snapshot.integrated_task_ids == ("TASK-A", "TASK-B")
    assert [attempt.task_commit for attempt in recovered_snapshot.attempts] == [
        result_a.commit_sha,
        result_b.commit_sha,
    ]
    with pytest.raises(MergeQueueError, match="already integrated"):
        recovered.integrate([result_a])
