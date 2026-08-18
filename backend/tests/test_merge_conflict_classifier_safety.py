from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.conflict_classifier import (
    GitMergeConflictClassifier,
    MergeConflictClassificationError,
)
from app.runtime.merge_queue import TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "shared.txt").write_text("VALUE=base\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def _task(task_id: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Update shared.txt for {task_id}.",
        readable_files=["**"],
        writable_files=["shared.txt"],
        readonly_files=[],
        acceptance_criteria=["shared.txt has the task result"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _run_result(task: models.TaskContract) -> SingleTaskRunResult:
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


def _finish_worker(
    manager: workspace.TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: models.TaskContract,
    *,
    content: str | None,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    target = task_workspace.resolve_path("shared.txt")
    if content is None:
        target.unlink()
    else:
        target.write_text(content, encoding="utf-8")
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
        run_result=_run_result(task),
        duration_ms=1,
    )


def _conflicted_queue(
    tmp_path: Path,
    *,
    first_content: str | None,
    second_content: str | None,
) -> tuple[
    workspace.LocalGitWorkspace,
    models.MergeQueueSnapshot,
    models.WorkerTaskResult,
]:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    dag = models.TaskDAG(
        tasks=(
            models.TaskNode(task=task_a, depends_on=()),
            models.TaskNode(task=task_b, depends_on=()),
        )
    )
    scheduler = DAGScheduler(dag)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(
        manager,
        scheduler,
        task_a,
        content=first_content,
    )
    result_b = _finish_worker(
        manager,
        scheduler,
        task_b,
        content=second_content,
    )
    queue = TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id="run-001",
    )
    snapshot = queue.integrate([result_a, result_b])
    assert snapshot.stopped is True
    return base, snapshot, result_b


def test_delete_modify_conflict_is_derived_from_inverse_stage_shape(tmp_path: Path) -> None:
    base, snapshot, _ = _conflicted_queue(
        tmp_path,
        first_content=None,
        second_content="VALUE=task-b\n",
    )

    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    assert evidence.conflicting_paths == ("shared.txt",)
    assert evidence.files[0].stage_shape is models.MergeConflictStageShape.DELETE_MODIFY
    assert [stage.stage for stage in evidence.files[0].stages] == [1, 3]
    assert [stage.side for stage in evidence.files[0].stages] == [
        models.MergeConflictStageSide.BASE,
        models.MergeConflictStageSide.TASK,
    ]
    assert base.changed_files() == []


def test_integration_ref_movement_after_snapshot_fails_closed(tmp_path: Path) -> None:
    base, snapshot, result_b = _conflicted_queue(
        tmp_path,
        first_content="VALUE=task-a\n",
        second_content="VALUE=task-b\n",
    )
    _git(
        base.root,
        "update-ref",
        snapshot.integration_ref,
        result_b.commit_sha or "",
        snapshot.head_commit,
    )

    with pytest.raises(
        MergeConflictClassificationError,
        match="integration ref moved after the queue snapshot was captured",
    ):
        GitMergeConflictClassifier(base).classify(snapshot)

    assert base.changed_files() == []
