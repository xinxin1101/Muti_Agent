from __future__ import annotations

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.api.models import ProductDiffKind
from app.api.service import ProductDiffUnavailableError, ProductRuntimeService
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.serialization import canonical_payload
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.workspace import LocalGitWorkspace


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> tuple[LocalGitWorkspace, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "DevFlow Test")
    _git(root, "config", "user.email", "devflow@example.com")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    _git(root, "add", "app.py")
    _git(root, "commit", "-m", "task")
    head = _git(root, "rev-parse", "HEAD")
    return LocalGitWorkspace(root), base, head


def _integration_commit(
    workspace: LocalGitWorkspace,
    base: str,
    task_commit: str,
    *,
    reverse_parents: bool = False,
) -> str:
    root = workspace.root
    tree = _git(root, "rev-parse", f"{task_commit}^{{tree}}")
    parents = (
        (task_commit, base)
        if reverse_parents
        else (base, task_commit)
    )
    return _git(
        root,
        "commit-tree",
        tree,
        "-p",
        parents[0],
        "-p",
        parents[1],
        "-m",
        "DevFlow integration test",
    )


def _task() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        objective="Change app.py.",
        readable_files=["app.py"],
        writable_files=["app.py"],
        readonly_files=[],
        acceptance_criteria=["value changes"],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _success_result() -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id="task-1",
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="pending"),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="succeeded"),
        ],
    )


class FakeStore:
    def __init__(self, snapshot: PersistedRunSnapshot) -> None:
        self.snapshot = snapshot

    async def load_run(self, run_id):
        assert run_id == self.snapshot.run_id
        return self.snapshot

    async def dispose(self) -> None:
        return None


class FakeResolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self.workspace = workspace

    def resolve(self, project_id):
        return self.workspace


class NotUsed:
    async def dispose(self) -> None:
        return None

    def __getattr__(self, name):
        raise AssertionError(f"unexpected dependency use: {name}")


def _snapshot(
    base: str,
    head: str,
    *,
    integration_commit: str | None = None,
) -> PersistedRunSnapshot:
    run_id = uuid4()
    project_id = uuid4()
    task = _task()
    worker_payload = WorkerExecutionEvidence(
        dispatch_id=uuid4(),
        run_id=run_id,
        task_id=task.task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base,
        branch_name="devflow/task-1",
        commit_sha=head,
        run_result=_success_result(),
        duration_ms=10,
    )
    worker_raw, worker_digest = canonical_payload(worker_payload)
    evidence = [
        PersistedEvidence(
            id=1,
            run_id=run_id,
            task_id=task.task_id,
            evidence_key="worker:1",
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            stage="worker",
            schema_version=1,
            payload=worker_raw,
            payload_sha256=worker_digest,
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
    ]

    if integration_commit is not None:
        merge_payload = MergeQueueSnapshot(
            integration_ref="refs/devflow/integration/test",
            run_base_commit=base,
            head_commit=integration_commit,
            integrated_task_ids=(task.task_id,),
            attempts=(
                MergeQueueAttempt(
                    sequence=0,
                    task_id=task.task_id,
                    task_branch="devflow/task-1",
                    task_base_commit=base,
                    task_commit=head,
                    previous_integration_commit=base,
                    outcome=MergeAttemptOutcome.INTEGRATED,
                    integration_commit=integration_commit,
                ),
            ),
            stopped=False,
        )
        merge_raw, merge_digest = canonical_payload(merge_payload)
        evidence.append(
            PersistedEvidence(
                id=2,
                run_id=run_id,
                task_id=None,
                evidence_key="merge:1",
                kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
                stage="integration",
                schema_version=1,
                payload=merge_raw,
                payload_sha256=merge_digest,
                created_at=datetime(2026, 8, 19, tzinfo=UTC),
            )
        )

    return PersistedRunSnapshot(
        run_id=run_id,
        project_id=project_id,
        repository_url="https://example.com/repo.git",
        default_branch="main",
        base_commit=base,
        status=PersistedRunStatus.RUNNING,
        tasks=(
            PersistedTask(
                task=task,
                contract_sha256=canonical_payload(task)[1],
                created_at=datetime(2026, 8, 19, tzinfo=UTC),
            ),
        ),
        evidence=tuple(evidence),
        started_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def _service(snapshot: PersistedRunSnapshot, workspace: LocalGitWorkspace) -> ProductRuntimeService:
    return ProductRuntimeService(
        catalog=NotUsed(),  # type: ignore[arg-type]
        evidence_store=FakeStore(snapshot),  # type: ignore[arg-type]
        dag_store=NotUsed(),  # type: ignore[arg-type]
        provisioner=NotUsed(),  # type: ignore[arg-type]
        workspace_resolver=FakeResolver(workspace),  # type: ignore[arg-type]
        dispatcher=NotUsed(),  # type: ignore[arg-type]
    )


def test_task_diff_pair_is_resolved_from_worker_evidence_not_browser_sha(tmp_path: Path) -> None:
    workspace, base, head = _repo(tmp_path)
    snapshot = _snapshot(base, head)
    service = _service(snapshot, workspace)

    result = asyncio.run(
        service.get_task_diff(snapshot.run_id, "task-1", kind=ProductDiffKind.TASK)
    )

    assert result.base_commit == base
    assert result.head_commit == head
    assert result.evidence_basis.value == "WORKER_EXECUTION"
    assert result.source_evidence_id == 1
    assert result.changed_file_count == 1
    assert result.files[0].path == "app.py"
    assert "+value = 2" in (result.files[0].patch or "")


def test_task_diff_rejects_commit_not_directly_based_on_recorded_base(tmp_path: Path) -> None:
    workspace, base, task_commit = _repo(tmp_path)
    (workspace.root / "app.py").write_text("value = 3\n", encoding="utf-8")
    _git(workspace.root, "add", "app.py")
    _git(workspace.root, "commit", "-m", "unexpected intermediate task")
    later_commit = _git(workspace.root, "rev-parse", "HEAD")
    assert _git(workspace.root, "rev-parse", f"{later_commit}^") == task_commit

    snapshot = _snapshot(base, later_commit)
    service = _service(snapshot, workspace)

    with pytest.raises(PersistenceCorruptionError, match="recorded task base as sole parent"):
        asyncio.run(
            service.get_task_diff(
                snapshot.run_id,
                "task-1",
                kind=ProductDiffKind.TASK,
            )
        )


def test_integration_diff_revalidates_the_recorded_two_parent_commit(tmp_path: Path) -> None:
    workspace, base, task_commit = _repo(tmp_path)
    integration_commit = _integration_commit(workspace, base, task_commit)
    snapshot = _snapshot(base, task_commit, integration_commit=integration_commit)
    service = _service(snapshot, workspace)

    result = asyncio.run(
        service.get_task_diff(
            snapshot.run_id,
            "task-1",
            kind=ProductDiffKind.INTEGRATION,
        )
    )

    assert result.base_commit == base
    assert result.head_commit == integration_commit
    assert result.evidence_basis.value == "MERGE_QUEUE_SNAPSHOT"
    assert result.source_evidence_id == 2
    assert result.files[0].path == "app.py"
    assert "+value = 2" in (result.files[0].patch or "")


def test_integration_diff_rejects_reversed_parent_order(tmp_path: Path) -> None:
    workspace, base, task_commit = _repo(tmp_path)
    integration_commit = _integration_commit(
        workspace,
        base,
        task_commit,
        reverse_parents=True,
    )
    snapshot = _snapshot(base, task_commit, integration_commit=integration_commit)
    service = _service(snapshot, workspace)

    with pytest.raises(PersistenceCorruptionError, match="recorded parent pair"):
        asyncio.run(
            service.get_task_diff(
                snapshot.run_id,
                "task-1",
                kind=ProductDiffKind.INTEGRATION,
            )
        )


def test_integration_diff_is_unavailable_without_merge_queue_evidence(tmp_path: Path) -> None:
    workspace, base, head = _repo(tmp_path)
    snapshot = _snapshot(base, head)
    service = _service(snapshot, workspace)

    with pytest.raises(ProductDiffUnavailableError, match="integration diff"):
        asyncio.run(
            service.get_task_diff(
                snapshot.run_id,
                "task-1",
                kind=ProductDiffKind.INTEGRATION,
            )
        )
