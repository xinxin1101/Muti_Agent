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


def _repository(tmp_path: Path, files: dict[str, str]) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def _task(task_id: str, *writable_files: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Apply {task_id} repository change.",
        readable_files=["**"],
        writable_files=list(writable_files),
        readonly_files=[],
        acceptance_criteria=[f"{task_id} change is present"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _dag(*tasks: models.TaskContract) -> models.TaskDAG:
    return models.TaskDAG(
        tasks=tuple(models.TaskNode(task=task, depends_on=()) for task in tasks)
    )


def _run_result(task: models.TaskContract, changed_files: list[str]) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=changed_files,
    )


def _finish_write(
    manager: workspace.TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: models.TaskContract,
    path: str,
    content: str,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    target = task_workspace.resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
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
        run_result=_run_result(task, [path]),
        duration_ms=1,
    )


def _finish_delete(
    manager: workspace.TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: models.TaskContract,
    path: str,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path(path).unlink()
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
        run_result=_run_result(task, [path]),
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


def _content_conflict(
    tmp_path: Path,
    *,
    path: str = "shared.txt",
    integration_id: str = "run-001",
) -> tuple[
    workspace.LocalGitWorkspace,
    DAGScheduler,
    workspace.TaskWorktreeManager,
    TopologicalMergeQueue,
    models.MergeQueueSnapshot,
    models.WorkerTaskResult,
]:
    base = _repository(tmp_path, {path: "VALUE=base\n"})
    task_a = _task("TASK-A", path)
    task_b = _task("TASK-B", path)
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, path, "VALUE=task-a\n")
    result_b = _finish_write(manager, scheduler, task_b, path, "VALUE=task-b\n")
    queue = _queue(
        scheduler,
        manager,
        base,
        integration_id=integration_id,
    )
    snapshot = queue.integrate([result_a, result_b])
    assert snapshot.stopped is True
    return base, scheduler, manager, queue, snapshot, result_b


def test_content_conflict_has_paths_types_and_three_git_stages(tmp_path: Path) -> None:
    base, _, _, _, snapshot, result_b = _content_conflict(tmp_path)
    head_before = _git(base.root, "rev-parse", "HEAD")

    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    assert evidence.integration_head == snapshot.head_commit
    assert evidence.task_commit == result_b.commit_sha
    assert evidence.conflict_ref == "refs/devflow/integration-conflicts/run-001"
    assert _git(base.root, "rev-parse", evidence.conflict_ref) == evidence.marker_commit
    assert evidence.conflicting_paths == ("shared.txt",)
    assert "CONFLICT (contents)" in evidence.conflict_types
    assert [stage.stage for stage in evidence.files[0].stages] == [1, 2, 3]
    assert [stage.side for stage in evidence.files[0].stages] == [
        models.MergeConflictStageSide.BASE,
        models.MergeConflictStageSide.INTEGRATION,
        models.MergeConflictStageSide.TASK,
    ]
    assert evidence.git_exit_code == 1
    assert "\\0" in evidence.raw_git_evidence
    assert _git(base.root, "rev-parse", "HEAD") == head_before
    assert base.changed_files() == []


def test_add_add_conflict_has_only_integration_and_task_stages(tmp_path: Path) -> None:
    base = _repository(tmp_path, {"README.md": "base\n"})
    task_a = _task("TASK-A", "new.txt")
    task_b = _task("TASK-B", "new.txt")
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, "new.txt", "from-a\n")
    result_b = _finish_write(manager, scheduler, task_b, "new.txt", "from-b\n")
    snapshot = _queue(scheduler, manager, base).integrate([result_a, result_b])

    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    assert evidence.conflicting_paths == ("new.txt",)
    assert "CONFLICT (add/add)" in evidence.conflict_types
    assert [stage.stage for stage in evidence.files[0].stages] == [2, 3]
    assert [stage.side for stage in evidence.files[0].stages] == [
        models.MergeConflictStageSide.INTEGRATION,
        models.MergeConflictStageSide.TASK,
    ]
    assert base.changed_files() == []


def test_modify_delete_conflict_preserves_missing_task_stage(tmp_path: Path) -> None:
    base = _repository(tmp_path, {"shared.txt": "VALUE=base\n"})
    task_a = _task("TASK-A", "shared.txt")
    task_b = _task("TASK-B", "shared.txt")
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(
        manager,
        scheduler,
        task_a,
        "shared.txt",
        "VALUE=modified\n",
    )
    result_b = _finish_delete(manager, scheduler, task_b, "shared.txt")
    snapshot = _queue(scheduler, manager, base).integrate([result_a, result_b])

    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    assert evidence.conflicting_paths == ("shared.txt",)
    assert "CONFLICT (modify/delete)" in evidence.conflict_types
    assert [stage.stage for stage in evidence.files[0].stages] == [1, 2]
    assert [stage.side for stage in evidence.files[0].stages] == [
        models.MergeConflictStageSide.BASE,
        models.MergeConflictStageSide.INTEGRATION,
    ]
    assert base.changed_files() == []


def test_nul_format_preserves_spaces_and_unicode_in_conflict_paths(tmp_path: Path) -> None:
    path = "dir/conflict path 植物.txt"
    base, _, _, _, snapshot, _ = _content_conflict(tmp_path, path=path)

    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    assert evidence.conflicting_paths == (path,)
    assert evidence.files[0].path == path
    assert any(path in message.paths for message in evidence.messages)


def test_conflict_classification_is_reproducible_after_queue_recovery(tmp_path: Path) -> None:
    base, scheduler, manager, _, snapshot, _ = _content_conflict(
        tmp_path,
        integration_id="recover-conflict",
    )
    first = GitMergeConflictClassifier(base).classify(snapshot)

    recovered_queue = _queue(
        scheduler,
        manager,
        base,
        integration_id="recover-conflict",
    )
    recovered_snapshot = recovered_queue.snapshot()
    second = GitMergeConflictClassifier(base).classify(recovered_snapshot)

    assert recovered_snapshot.stopped is True
    assert second == first


def test_conflicted_task_branch_movement_fails_closed(tmp_path: Path) -> None:
    base, _, _, _, snapshot, result_b = _content_conflict(tmp_path)

    _git(
        base.root,
        "update-ref",
        f"refs/heads/{result_b.branch_name}",
        result_b.base_commit or "",
        result_b.commit_sha or "",
    )

    with pytest.raises(
        MergeConflictClassificationError,
        match="task branch moved after worker finalization",
    ):
        GitMergeConflictClassifier(base).classify(snapshot)

    assert base.changed_files() == []


def test_classifier_rejects_snapshot_without_terminal_conflict(tmp_path: Path) -> None:
    base = _repository(tmp_path, {"a.txt": "A=base\n"})
    task = _task("TASK-A", "a.txt")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result = _finish_write(manager, scheduler, task, "a.txt", "A=done\n")
    snapshot = _queue(scheduler, manager, base).integrate([result])

    with pytest.raises(
        MergeConflictClassificationError,
        match="does not contain a terminal conflict",
    ):
        GitMergeConflictClassifier(base).classify(snapshot)


def test_raw_git_evidence_is_bounded_and_marks_truncation(tmp_path: Path) -> None:
    base, _, _, _, snapshot, _ = _content_conflict(tmp_path)

    evidence = GitMergeConflictClassifier(base, raw_evidence_limit=256).classify(snapshot)

    assert evidence.raw_git_evidence_truncated is True
    assert len(evidence.raw_git_evidence) <= 256
    assert evidence.raw_git_evidence.endswith("...[truncated]")


def test_stage_side_model_rejects_contradictory_semantics() -> None:
    with pytest.raises(ValueError, match="stage side"):
        models.MergeConflictStage(
            stage=2,
            side=models.MergeConflictStageSide.TASK,
            mode="100644",
            object_id="a" * 40,
        )
