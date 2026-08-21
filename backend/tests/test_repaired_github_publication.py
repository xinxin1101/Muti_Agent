from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.api.publication import resolve_github_publication_intent
from app.models.developer import DeveloperRunResult, DeveloperStopReason
from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.integration_gate import HumanGateDecision, HumanIntegrationDecision
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.publication import GitHubPublicationSourceBasis
from app.models.task import TaskContract
from app.models.verification import VerificationResult
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.workspace import LocalGitWorkspace

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
FINGERPRINT = "a" * 64
POLICY_FINGERPRINT = "b" * 64


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _commit_tree(root: Path, tree: str, *parents: str, message: str) -> str:
    arguments = ["commit-tree", tree]
    for parent in parents:
        arguments.extend(["-p", parent])
    arguments.extend(["-m", message])
    return _git(root, *arguments)


def _repository(tmp_path: Path) -> tuple[LocalGitWorkspace, dict[str, str]]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "devflow@example.test")
    _git(root, "config", "user.name", "DevFlow Test")
    (root / "one.txt").write_text("base-one\n", encoding="utf-8")
    (root / "two.txt").write_text("base-two\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "checkout", "-b", "task-one", base)
    (root / "one.txt").write_text("task-one\n", encoding="utf-8")
    _git(root, "add", "one.txt")
    _git(root, "commit", "-m", "task one")
    task_one = _git(root, "rev-parse", "HEAD")

    base_tree = _git(root, "rev-parse", f"{base}^{{tree}}")
    marker = _commit_tree(root, base_tree, base, message="conflict marker")
    decision_commit = _commit_tree(root, base_tree, marker, message="human decision")
    task_one_tree = _git(root, "rev-parse", f"{task_one}^{{tree}}")
    repair = _commit_tree(root, task_one_tree, base, task_one, message="repaired integration")

    _git(root, "checkout", "--detach", repair)
    (root / "two.txt").write_text("task-two\n", encoding="utf-8")
    _git(root, "add", "two.txt")
    _git(root, "commit", "-m", "task two")
    task_two = _git(root, "rev-parse", "HEAD")
    task_two_tree = _git(root, "rev-parse", f"{task_two}^{{tree}}")
    final = _commit_tree(root, task_two_tree, repair, task_two, message="integrate task two")

    return LocalGitWorkspace(root), {
        "base": base,
        "task_one": task_one,
        "marker": marker,
        "decision": decision_commit,
        "repair": repair,
        "task_two": task_two,
        "final": final,
    }


def _task(task_id: str, path: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Update {path}.",
        readable_files=[path],
        writable_files=[path],
        readonly_files=[],
        acceptance_criteria=[f"{path} is updated"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _snapshot(
    commits: dict[str, str],
    *,
    include_repair: bool = True,
    include_decision: bool = True,
) -> PersistedRunSnapshot:
    task_one = _task("task-one", "one.txt")
    task_two = _task("task-two", "two.txt")
    conflict_failure = FailureReport(
        failure_type=FailureType.MERGE_CONFLICT,
        source=FailureSource.RUNTIME,
        message="Git reported a merge conflict.",
        retryable=False,
        evidence=["bounded conflict evidence"],
    )
    merge = MergeQueueSnapshot(
        integration_ref="refs/devflow/integration/run-test",
        run_base_commit=commits["base"],
        head_commit=commits["final"],
        integrated_task_ids=("task-one", "task-two"),
        attempts=(
            MergeQueueAttempt(
                sequence=0,
                task_id="task-one",
                task_branch="devflow/task/task-one",
                task_base_commit=commits["base"],
                task_commit=commits["task_one"],
                previous_integration_commit=commits["base"],
                outcome=MergeAttemptOutcome.CONFLICT,
                failure=conflict_failure,
            ),
            MergeQueueAttempt(
                sequence=1,
                task_id="task-one",
                task_branch="devflow/task/task-one",
                task_base_commit=commits["base"],
                task_commit=commits["task_one"],
                previous_integration_commit=commits["base"],
                outcome=MergeAttemptOutcome.REPAIRED,
                integration_commit=commits["repair"],
                conflict_marker_commit=commits["marker"],
                conflict_evidence_fingerprint=FINGERPRINT,
                policy_fingerprint=POLICY_FINGERPRINT,
                human_decision_commit=commits["decision"],
            ),
            MergeQueueAttempt(
                sequence=2,
                task_id="task-two",
                task_branch="devflow/task/task-two",
                task_base_commit=commits["repair"],
                task_commit=commits["task_two"],
                previous_integration_commit=commits["repair"],
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=commits["final"],
            ),
        ),
        stopped=False,
    )
    decision = HumanIntegrationDecision(
        decision=HumanGateDecision.AUTHORIZE_REPAIR,
        actor="maintainer",
        note="Approved bounded repair",
        decision_ref="refs/devflow/integration-decisions/run-test",
        decision_commit=commits["decision"],
        evidence_fingerprint=FINGERPRINT,
        policy_fingerprint=POLICY_FINGERPRINT,
        conflict_marker_commit=commits["marker"],
    )
    repair = IntegrationConflictRepairEvidence(
        run_id=RUN_ID,
        task_id="task-one",
        integration_head=commits["base"],
        task_commit=commits["task_one"],
        conflict_marker_commit=commits["marker"],
        conflict_evidence_fingerprint=FINGERPRINT,
        policy_fingerprint=POLICY_FINGERPRINT,
        human_decision_commit=commits["decision"],
        conflicting_paths=("one.txt",),
        repair_commit=commits["repair"],
        changed_files=("one.txt",),
        developer_run=DeveloperRunResult(
            stop_reason=DeveloperStopReason.MODEL_STOP,
            iterations=1,
            tool_calls=1,
            changed_files=["one.txt"],
        ),
        verification=VerificationResult(passed=True, checks=[]),
    )

    evidence = [
        PersistedEvidence(
            id=1,
            run_id=RUN_ID,
            evidence_key="integration:complete",
            kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
            stage="integration",
            schema_version=1,
            payload=merge.model_dump(mode="json"),
            payload_sha256="c" * 64,
            created_at=NOW,
        )
    ]
    if include_decision:
        evidence.append(
            PersistedEvidence(
                id=2,
                run_id=RUN_ID,
                task_id="task-one",
                evidence_key="human:decision",
                kind=PersistenceEvidenceKind.HUMAN_DECISION,
                stage="integration",
                schema_version=1,
                payload=decision.model_dump(mode="json"),
                payload_sha256="d" * 64,
                created_at=NOW,
            )
        )
    if include_repair:
        evidence.append(
            PersistedEvidence(
                id=3,
                run_id=RUN_ID,
                task_id="task-one",
                evidence_key="integration:repair",
                kind=PersistenceEvidenceKind.INTEGRATION_REPAIR,
                stage="integration_repair",
                schema_version=1,
                payload=repair.model_dump(mode="json"),
                payload_sha256="e" * 64,
                created_at=NOW,
            )
        )

    return PersistedRunSnapshot(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        default_branch="main",
        base_commit=commits["base"],
        status=PersistedRunStatus.SUCCEEDED,
        tasks=(
            PersistedTask(task=task_one, contract_sha256="1" * 64, created_at=NOW),
            PersistedTask(task=task_two, contract_sha256="2" * 64, created_at=NOW),
        ),
        evidence=tuple(evidence),
        terminal_result={"status": "SUCCEEDED"},
        terminal_result_sha256="f" * 64,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
    )


def test_repaired_multi_task_run_is_publishable_only_with_bound_authority(
    tmp_path: Path,
) -> None:
    workspace, commits = _repository(tmp_path)

    intent = resolve_github_publication_intent(_snapshot(commits), workspace)

    assert intent.source_basis is GitHubPublicationSourceBasis.INTEGRATION
    assert intent.source_commit == commits["final"]

    with pytest.raises(PersistenceCorruptionError, match="integration repair evidence"):
        resolve_github_publication_intent(
            _snapshot(commits, include_repair=False),
            workspace,
        )

    with pytest.raises(PersistenceCorruptionError, match="human decision evidence"):
        resolve_github_publication_intent(
            _snapshot(commits, include_decision=False),
            workspace,
        )
