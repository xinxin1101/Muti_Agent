from __future__ import annotations

import subprocess
from pathlib import Path

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_gate import IntegrationHumanGate
from app.runtime.integration_policy import IntegrationConflictPolicy
from app.runtime.merge_queue import TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _task(task_id: str, path: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Update {path} for {task_id}.",
        readable_files=[path],
        writable_files=[path],
        readonly_files=[],
        acceptance_criteria=["Task-specific content is present."],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _run_result(task: models.TaskContract, path: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=[path],
    )


def _finish_binary_write(
    manager: workspace.TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: models.TaskContract,
    path: str,
    content: bytes,
) -> models.WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path(path).write_bytes(content)
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


def test_binary_blob_disguised_as_python_requires_human_gate(tmp_path: Path) -> None:
    path = "shared.py"
    root = tmp_path / "repository"
    root.mkdir()
    (root / path).write_bytes(b"\x00base\n")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    base = workspace.LocalGitWorkspace(root)
    task_a = _task("TASK-A", path)
    task_b = _task("TASK-B", path)
    dag = models.TaskDAG(
        tasks=(
            models.TaskNode(task=task_a, depends_on=()),
            models.TaskNode(task=task_b, depends_on=()),
        )
    )
    scheduler = DAGScheduler(dag)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_binary_write(
        manager,
        scheduler,
        task_a,
        path,
        b"\x00task-a\n",
    )
    result_b = _finish_binary_write(
        manager,
        scheduler,
        task_b,
        path,
        b"\x00task-b\n",
    )
    queue = TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id="binary-gate",
    )
    snapshot = queue.integrate([result_a, result_b])
    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    gate = IntegrationHumanGate(
        workspace=base,
        queue_snapshot=snapshot,
        scheduler=scheduler,
        evidence=evidence,
        policy=IntegrationConflictPolicy(automatic_repair_enabled=True),
    ).snapshot()

    assert evidence.files[0].stage_shape is models.MergeConflictStageShape.THREE_WAY
    assert gate.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert gate.repair_may_start is False
    assert gate.integration_may_advance is False
    assert any("not safe bounded UTF-8 text" in reason for reason in gate.policy.reasons)
    assert base.changed_files() == []
