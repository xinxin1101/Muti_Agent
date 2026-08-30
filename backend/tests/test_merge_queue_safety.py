from __future__ import annotations

import os
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
        acceptance_criteria=["shared.txt is updated"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _dag(*tasks: models.TaskContract) -> models.TaskDAG:
    return models.TaskDAG(tasks=tuple(models.TaskNode(task=task, depends_on=()) for task in tasks))


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
    content: str,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path("shared.txt").write_text(content, encoding="utf-8")
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


def _queue(
    scheduler: DAGScheduler,
    manager: workspace.TaskWorktreeManager,
    base: workspace.LocalGitWorkspace,
) -> TopologicalMergeQueue:
    return TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id="run-001",
    )


def test_task_branch_movement_is_rejected_before_ref_advances(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result = _finish_worker(manager, scheduler, task, "VALUE=task-a\n")
    queue = _queue(scheduler, manager, base)
    initial_head = queue.head_commit

    _git(
        base.root,
        "update-ref",
        f"refs/heads/{result.branch_name}",
        result.base_commit or "",
        result.commit_sha or "",
    )

    with pytest.raises(MergeQueueError, match="branch moved after worker finalization"):
        queue.integrate([result])

    assert queue.head_commit == initial_head
    assert _git(base.root, "rev-parse", queue.integration_ref) == initial_head
    assert queue.snapshot().attempts == ()


def test_real_merge_conflict_stops_and_recovers_from_git_evidence(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_worker(manager, scheduler, task_a, "VALUE=task-a\n")
    result_b = _finish_worker(manager, scheduler, task_b, "VALUE=task-b\n")
    queue = _queue(scheduler, manager, base)

    snapshot = queue.integrate([result_a, result_b])

    assert snapshot.stopped is True
    assert snapshot.integrated_task_ids == ("TASK-A",)
    assert len(snapshot.attempts) == 2
    conflict = snapshot.attempts[-1]
    assert conflict.outcome is models.MergeAttemptOutcome.CONFLICT
    assert conflict.integration_commit is None
    assert conflict.failure is not None
    assert conflict.failure.failure_type is models.FailureType.MERGE_CONFLICT
    assert any(evidence.startswith("conflict_marker=") for evidence in conflict.failure.evidence)
    assert snapshot.head_commit == snapshot.attempts[0].integration_commit
    assert _git(base.root, "rev-parse", queue.integration_ref) == snapshot.head_commit
    conflict_ref = "refs/devflow/integration-conflicts/run-001"
    assert _git_code(base.root, "show-ref", "--verify", "--quiet", conflict_ref) == 0
    assert base.changed_files() == []

    recovered = _queue(scheduler, manager, base)
    recovered_snapshot = recovered.snapshot()
    assert recovered_snapshot.stopped is True
    assert recovered_snapshot.integrated_task_ids == ("TASK-A",)
    assert [attempt.outcome for attempt in recovered_snapshot.attempts] == [
        models.MergeAttemptOutcome.INTEGRATED,
        models.MergeAttemptOutcome.CONFLICT,
    ]
    assert recovered_snapshot.attempts[-1].task_id == "TASK-B"
    assert recovered_snapshot.head_commit == snapshot.head_commit

    with pytest.raises(MergeQueueError, match="stopped after an unresolved integration conflict"):
        recovered.integrate([])


def test_forged_integration_commit_with_wrong_tree_is_rejected_on_recovery(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result = _finish_worker(manager, scheduler, task, "VALUE=task-a\n")
    queue = _queue(scheduler, manager, base)
    snapshot = queue.integrate([result])
    attempt = snapshot.attempts[0]
    previous_head = attempt.previous_integration_commit
    wrong_tree = _git(base.root, "rev-parse", f"{previous_head}^{{tree}}")
    message = (
        f"DevFlow integrate {result.task_id}\n\n"
        f"DevFlow-Task: {result.task_id}\n"
        f"DevFlow-Task-Branch: {result.branch_name}\n"
        f"DevFlow-Task-Base: {result.base_commit}\n"
        f"DevFlow-Task-Commit: {result.commit_sha}"
    )
    forged = _git(
        base.root,
        "commit-tree",
        wrong_tree,
        "-p",
        previous_head,
        "-p",
        result.commit_sha or "",
        "-m",
        message,
    )
    _git(
        base.root,
        "update-ref",
        queue.integration_ref,
        forged,
        snapshot.head_commit,
    )

    with pytest.raises(
        MergeQueueError,
        match="integration commit tree does not match deterministic Git merge",
    ):
        _queue(scheduler, manager, base)

    assert base.changed_files() == []


def test_external_integration_ref_movement_is_detected(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result = _finish_worker(manager, scheduler, task, "VALUE=task-a\n")
    queue = _queue(scheduler, manager, base)

    _git(
        base.root,
        "update-ref",
        queue.integration_ref,
        result.commit_sha or "",
        queue.head_commit,
    )

    with pytest.raises(MergeQueueError, match="integration ref moved outside"):
        queue.integrate([result])


def test_object_level_integration_does_not_invoke_commit_hooks(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("hook executable-bit semantics are POSIX-specific")

    base = _repository(tmp_path)
    sentinel = tmp_path / "hook-ran.txt"
    hook = base.root / ".git" / "hooks" / "post-commit"
    hook.write_text(f"#!/bin/sh\necho ran > '{sentinel}'\n", encoding="utf-8")
    hook.chmod(0o755)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result = _finish_worker(manager, scheduler, task, "VALUE=task-a\n")
    queue = _queue(scheduler, manager, base)

    queue.integrate([result])

    assert not sentinel.exists()
    assert base.changed_files() == []


def test_invalid_integration_id_is_rejected_without_creating_ref(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag(task))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    with pytest.raises(ValueError, match="integration_id"):
        TopologicalMergeQueue(
            scheduler=scheduler,
            worktrees=manager,
            base_workspace=base,
            integration_id="../unsafe",
        )

    assert (
        _git_code(
            base.root,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/devflow/integration/unsafe",
        )
        == 1
    )
