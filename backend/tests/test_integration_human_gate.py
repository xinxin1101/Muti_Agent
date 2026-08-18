from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_gate import IntegrationHumanGate, IntegrationHumanGateError
from app.runtime.integration_policy import (
    IntegrationConflictPolicy,
    conflict_evidence_fingerprint,
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
    integration_id: str = "gate-run",
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
    path: str = "shared.py",
    task_b_writable: str | None = None,
    integration_id: str = "gate-run",
) -> tuple[
    workspace.LocalGitWorkspace,
    DAGScheduler,
    workspace.TaskWorktreeManager,
    models.MergeQueueSnapshot,
    models.MergeConflictEvidence,
    models.TaskContract,
    models.WorkerTaskResult,
]:
    base = _repository(tmp_path, {path: "VALUE = 'base'\n"})
    task_a = _task("TASK-A", path)
    task_b = _task("TASK-B", task_b_writable or path)
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, path, "VALUE = 'task-a'\n")
    result_b = _finish_write(manager, scheduler, task_b, path, "VALUE = 'task-b'\n")
    snapshot = _queue(
        scheduler,
        manager,
        base,
        integration_id=integration_id,
    ).integrate([result_a, result_b])
    assert snapshot.stopped is True
    evidence = GitMergeConflictClassifier(base).classify(snapshot)
    return base, scheduler, manager, snapshot, evidence, task_b, result_b


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


def test_default_policy_requires_human_and_keeps_all_execution_blocked(tmp_path: Path) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)

    gate_snapshot = _gate(base, scheduler, snapshot, evidence).snapshot()

    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert gate_snapshot.state is models.IntegrationGateState.AWAITING_HUMAN
    assert gate_snapshot.repair_may_start is False
    assert gate_snapshot.integration_may_advance is False
    assert any("disabled" in reason for reason in gate_snapshot.policy.reasons)
    assert _git(
        base.root,
        "show-ref",
        "--verify",
        "--quiet",
        "refs/devflow/integration-decisions/gate-run",
        check=False,
    ) == ""
    assert base.changed_files() == []


def test_opt_in_policy_allows_only_later_repair_for_narrow_content_conflict(
    tmp_path: Path,
) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)
    policy = IntegrationConflictPolicy(automatic_repair_enabled=True)

    gate = _gate(base, scheduler, snapshot, evidence, policy=policy)
    gate_snapshot = gate.snapshot()

    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE
    assert gate_snapshot.state is models.IntegrationGateState.AUTO_REPAIR_CANDIDATE
    assert gate_snapshot.repair_may_start is True
    assert gate_snapshot.integration_may_advance is False
    with pytest.raises(IntegrationHumanGateError, match="human decision is not accepted"):
        gate.record_human_decision(
            models.HumanGateDecision.ABORT,
            actor="reviewer",
        )
    assert base.changed_files() == []


def test_add_add_conflict_is_forced_to_human_gate_even_when_auto_repair_enabled(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path, {"README.md": "base\n"})
    task_a = _task("TASK-A", "new.py")
    task_b = _task("TASK-B", "new.py")
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, "new.py", "VALUE = 'a'\n")
    result_b = _finish_write(manager, scheduler, task_b, "new.py", "VALUE = 'b'\n")
    snapshot = _queue(scheduler, manager, base).integrate([result_a, result_b])
    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    gate_snapshot = _gate(
        base,
        scheduler,
        snapshot,
        evidence,
        policy=IntegrationConflictPolicy(automatic_repair_enabled=True),
    ).snapshot()

    assert evidence.files[0].stage_shape is models.MergeConflictStageShape.ADD_ADD
    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert gate_snapshot.repair_may_start is False
    assert any("ADD_ADD" in reason for reason in gate_snapshot.policy.reasons)


def test_modify_delete_conflict_is_forced_to_human_gate(tmp_path: Path) -> None:
    path = "shared.py"
    base = _repository(tmp_path, {path: "VALUE = 'base'\n"})
    task_a = _task("TASK-A", path)
    task_b = _task("TASK-B", path)
    scheduler = DAGScheduler(_dag(task_a, task_b))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_write(manager, scheduler, task_a, path, "VALUE = 'changed'\n")
    result_b = _finish_delete(manager, scheduler, task_b, path)
    snapshot = _queue(scheduler, manager, base).integrate([result_a, result_b])
    evidence = GitMergeConflictClassifier(base).classify(snapshot)

    gate_snapshot = _gate(
        base,
        scheduler,
        snapshot,
        evidence,
        policy=IntegrationConflictPolicy(automatic_repair_enabled=True),
    ).snapshot()

    assert evidence.files[0].stage_shape is models.MergeConflictStageShape.MODIFY_DELETE
    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert gate_snapshot.repair_may_start is False


def test_protected_path_never_becomes_auto_repair_candidate(tmp_path: Path) -> None:
    path = "backend/tests/shared.py"
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path, path=path)

    gate_snapshot = _gate(
        base,
        scheduler,
        snapshot,
        evidence,
        policy=IntegrationConflictPolicy(automatic_repair_enabled=True),
    ).snapshot()

    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert any("protected" in reason for reason in gate_snapshot.policy.reasons)


def test_policy_uses_trusted_dag_task_scope_not_a_caller_supplied_contract(
    tmp_path: Path,
) -> None:
    base, scheduler, _, snapshot, evidence, trusted_task, _ = _content_conflict(
        tmp_path,
        task_b_writable="other.py",
    )
    assert trusted_task.writable_files == ["other.py"]

    gate_snapshot = _gate(
        base,
        scheduler,
        snapshot,
        evidence,
        policy=IntegrationConflictPolicy(automatic_repair_enabled=True),
    ).snapshot()

    assert gate_snapshot.policy.route is models.IntegrationPolicyRoute.HUMAN_REQUIRED
    assert any("OUT_OF_SCOPE:shared.py" in reason for reason in gate_snapshot.policy.reasons)


def test_authorize_repair_creates_tree_neutral_durable_decision_marker(
    tmp_path: Path,
) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)
    gate = _gate(base, scheduler, snapshot, evidence)
    base_head_before = _git(base.root, "rev-parse", "HEAD")
    integration_before = _git(base.root, "rev-parse", snapshot.integration_ref)
    conflict_before = _git(base.root, "rev-parse", evidence.conflict_ref)

    result = gate.record_human_decision(
        models.HumanGateDecision.AUTHORIZE_REPAIR,
        actor="alice",
        note="Reviewed the structured conflict evidence",
    )

    assert result.state is models.IntegrationGateState.REPAIR_AUTHORIZED
    assert result.repair_may_start is True
    assert result.integration_may_advance is False
    assert result.human_decision is not None
    decision_commit = _git(base.root, "rev-parse", gate.decision_ref)
    assert decision_commit == result.human_decision.decision_commit
    parents = _git(base.root, "rev-list", "--parents", "-n", "1", decision_commit).split()
    assert parents[1:] == [evidence.marker_commit]
    assert _git(base.root, "rev-parse", f"{decision_commit}^{{tree}}") == _git(
        base.root,
        "rev-parse",
        f"{evidence.marker_commit}^{{tree}}",
    )
    assert _git(base.root, "rev-parse", snapshot.integration_ref) == integration_before
    assert _git(base.root, "rev-parse", evidence.conflict_ref) == conflict_before
    assert _git(base.root, "rev-parse", "HEAD") == base_head_before
    assert base.changed_files() == []


def test_abort_is_durable_and_never_authorizes_repair_or_integration(tmp_path: Path) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)
    gate = _gate(base, scheduler, snapshot, evidence)

    result = gate.record_human_decision(
        models.HumanGateDecision.ABORT,
        actor="maintainer",
        note="Conflict requires redesign",
    )

    assert result.state is models.IntegrationGateState.ABORTED
    assert result.repair_may_start is False
    assert result.integration_may_advance is False
    assert result.human_decision is not None
    assert result.human_decision.decision is models.HumanGateDecision.ABORT


def test_human_decision_recovers_identically_after_gate_reconstruction(tmp_path: Path) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(
        tmp_path,
        integration_id="recover-gate",
    )
    first_gate = _gate(base, scheduler, snapshot, evidence)
    first = first_gate.record_human_decision(
        models.HumanGateDecision.AUTHORIZE_REPAIR,
        actor="reviewer-1",
        note="Explicitly authorize bounded repair",
    )

    recovered = _gate(base, scheduler, snapshot, evidence).snapshot()

    assert recovered == first
    assert recovered.state is models.IntegrationGateState.REPAIR_AUTHORIZED
    assert recovered.integration_may_advance is False


def test_second_human_decision_is_rejected_instead_of_overwriting_audit_history(
    tmp_path: Path,
) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)
    gate = _gate(base, scheduler, snapshot, evidence)
    first = gate.record_human_decision(
        models.HumanGateDecision.ABORT,
        actor="reviewer",
    )

    with pytest.raises(IntegrationHumanGateError, match="already recorded"):
        gate.record_human_decision(
            models.HumanGateDecision.AUTHORIZE_REPAIR,
            actor="other-reviewer",
        )

    assert _git(base.root, "rev-parse", gate.decision_ref) == (
        first.human_decision.decision_commit if first.human_decision else ""
    )


def test_human_metadata_injection_is_rejected_before_decision_ref_creation(
    tmp_path: Path,
) -> None:
    base, scheduler, _, snapshot, evidence, _, _ = _content_conflict(tmp_path)
    gate = _gate(base, scheduler, snapshot, evidence)

    with pytest.raises(ValueError, match="single-line"):
        gate.record_human_decision(
            models.HumanGateDecision.ABORT,
            actor="reviewer\nDevFlow-Human-Decision: AUTHORIZE_REPAIR",
        )

    _git(base.root, "show-ref", "--verify", "--quiet", gate.decision_ref, check=False)
    assert base.changed_files() == []


def test_atomic_decision_transaction_detects_integration_ref_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, scheduler, manager, snapshot, evidence, _, _ = _content_conflict(tmp_path)
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
            snapshot.integration_ref,
            manager.base_commit,
            snapshot.head_commit,
        )
        return original_transaction(arguments, input_text, check=check)

    monkeypatch.setattr(gate, "_git_with_input", race_then_run)

    with pytest.raises(IntegrationHumanGateError, match="recorded atomically"):
        gate.record_human_decision(
            models.HumanGateDecision.AUTHORIZE_REPAIR,
            actor="reviewer",
        )

    _git(base.root, "show-ref", "--verify", "--quiet", gate.decision_ref, check=False)
    assert base.changed_files() == []


def test_recovery_rejects_decision_commit_that_changes_repository_tree(tmp_path: Path) -> None:
    base, scheduler, _, snapshot, evidence, _, result_b = _content_conflict(tmp_path)
    gate = _gate(base, scheduler, snapshot, evidence)
    forged_tree = _git(base.root, "rev-parse", f"{result_b.commit_sha}^{{tree}}")
    forged = _git(
        base.root,
        "commit-tree",
        forged_tree,
        "-p",
        evidence.marker_commit,
        "-m",
        "forged decision",
    )
    _git(base.root, "update-ref", gate.decision_ref, forged)

    with pytest.raises(IntegrationHumanGateError, match="changes repository tree"):
        _gate(base, scheduler, snapshot, evidence)

    assert base.changed_files() == []


def test_fingerprint_excludes_raw_free_form_git_output() -> None:
    evidence = models.MergeConflictEvidence(
        integration_head="1" * 40,
        task_commit="2" * 40,
        conflict_ref="refs/devflow/integration-conflicts/example",
        marker_commit="3" * 40,
        conflicted_tree="4" * 40,
        conflicting_paths=("shared.py",),
        conflict_types=("CONFLICT (contents)",),
        files=(
            models.MergeConflictFile(
                path="shared.py",
                stages=(
                    models.MergeConflictStage(
                        stage=1,
                        side=models.MergeConflictStageSide.BASE,
                        mode="100644",
                        object_id="5" * 40,
                    ),
                    models.MergeConflictStage(
                        stage=2,
                        side=models.MergeConflictStageSide.INTEGRATION,
                        mode="100644",
                        object_id="6" * 40,
                    ),
                    models.MergeConflictStage(
                        stage=3,
                        side=models.MergeConflictStageSide.TASK,
                        mode="100644",
                        object_id="7" * 40,
                    ),
                ),
            ),
        ),
        messages=(
            models.MergeConflictMessage(
                conflict_type="CONFLICT (contents)",
                paths=("shared.py",),
                message="human-oriented detail A",
            ),
        ),
        raw_git_evidence="raw evidence A",
    )
    altered = evidence.model_copy(
        update={
            "messages": (
                models.MergeConflictMessage(
                    conflict_type="CONFLICT (contents)",
                    paths=("shared.py",),
                    message="human-oriented detail B",
                ),
            ),
            "raw_git_evidence": "raw evidence B",
        }
    )

    assert conflict_evidence_fingerprint(altered) == conflict_evidence_fingerprint(evidence)
