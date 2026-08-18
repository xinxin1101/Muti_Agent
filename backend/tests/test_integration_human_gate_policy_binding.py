from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_gate import IntegrationHumanGate, IntegrationHumanGateError
from app.runtime.integration_policy import IntegrationConflictPolicy
from app.runtime.merge_queue import TopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed


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
        run_result=_run_result(task, path),
        duration_ms=1,
    )


def _conflict(
    tmp_path: Path,
    *,
    path: str,
    integration_id: str,
) -> tuple[
    workspace.LocalGitWorkspace,
    DAGScheduler,
    models.MergeQueueSnapshot,
    models.MergeConflictEvidence,
    models.WorkerTaskResult,
]:
    root = tmp_path / "repository"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    base = workspace.LocalGitWorkspace(root)
    task_a = _task("TASK-A", path)
    task_b = _task("TASK-B", path)
    scheduler = DAGScheduler(
        models.TaskDAG(
            tasks=(
                models.TaskNode(task=task_a, depends_on=()),
                models.TaskNode(task=task_b, depends_on=()),
            )
        )
    )
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, path, "VALUE = 'task-a'\n")
    result_b = _finish_write(manager, scheduler, task_b, path, "VALUE = 'task-b'\n")
    queue = TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id=integration_id,
    )
    snapshot = queue.integrate([result_a, result_b])
    evidence = GitMergeConflictClassifier(base).classify(snapshot)
    return base, scheduler, snapshot, evidence, result_b


def _gate(
    base: workspace.LocalGitWorkspace,
    scheduler: DAGScheduler,
    snapshot: models.MergeQueueSnapshot,
    evidence: models.MergeConflictEvidence,
    *,
    policy: IntegrationConflictPolicy | None = None,
) -> IntegrationHumanGate:
    return IntegrationHumanGate(
        workspace=base,
        queue_snapshot=snapshot,
        scheduler=scheduler,
        evidence=evidence,
        policy=policy,
    )


def _ref_exists(root: Path, ref_name: str) -> bool:
    return _git(root, "show-ref", "--verify", "--quiet", ref_name, check=False).returncode == 0


def test_protected_path_human_cannot_authorize_agent_repair(tmp_path: Path) -> None:
    base, scheduler, snapshot, evidence, _ = _conflict(
        tmp_path,
        path="tests/shared.py",
        integration_id="protected-human-gate",
    )
    gate = _gate(base, scheduler, snapshot, evidence)
    before = gate.snapshot()

    assert before.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert before.policy.human_repair_authorizable is False
    with pytest.raises(IntegrationHumanGateError, match="hard Agent Repair boundaries"):
        gate.record_human_decision(
            models.HumanGateDecision.AUTHORIZE_REPAIR,
            actor="maintainer",
        )

    aborted = gate.record_human_decision(
        models.HumanGateDecision.ABORT,
        actor="maintainer",
        note="Protected test conflict requires manual handling outside Agent Repair.",
    )
    assert aborted.state is models.IntegrationGateState.ABORTED
    assert aborted.repair_may_start is False
    assert aborted.integration_may_advance is False


def test_recovery_rejects_old_authorization_when_policy_semantics_change(
    tmp_path: Path,
) -> None:
    base, scheduler, snapshot, evidence, _ = _conflict(
        tmp_path,
        path="shared.py",
        integration_id="policy-drift",
    )
    original = _gate(base, scheduler, snapshot, evidence)
    authorized = original.record_human_decision(
        models.HumanGateDecision.AUTHORIZE_REPAIR,
        actor="reviewer",
    )
    assert authorized.policy.human_repair_authorizable is True

    stricter = IntegrationConflictPolicy(protected_prefixes=("shared.py",))
    with pytest.raises(IntegrationHumanGateError, match="policy_fingerprint"):
        _gate(
            base,
            scheduler,
            snapshot,
            evidence,
            policy=stricter,
        )


def test_decision_transaction_detects_task_branch_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, scheduler, snapshot, evidence, result_b = _conflict(
        tmp_path,
        path="shared.py",
        integration_id="task-branch-race",
    )
    gate = _gate(base, scheduler, snapshot, evidence)
    original_transaction = gate._git_with_input

    def race_then_run(
        arguments: list[str],
        input_text: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        _git(
            base.root,
            "update-ref",
            f"refs/heads/{result_b.branch_name}",
            result_b.base_commit or "",
            result_b.commit_sha or "",
        )
        return original_transaction(arguments, input_text, check=check)

    monkeypatch.setattr(gate, "_git_with_input", race_then_run)

    with pytest.raises(IntegrationHumanGateError, match="recorded atomically"):
        gate.record_human_decision(
            models.HumanGateDecision.AUTHORIZE_REPAIR,
            actor="reviewer",
        )

    assert _ref_exists(base.root, gate.decision_ref) is False
    assert base.changed_files() == []
