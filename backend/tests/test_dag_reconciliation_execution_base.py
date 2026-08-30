from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models import (
    MergeAttemptOutcome,
    MergeQueueAttempt,
    MergeQueueSnapshot,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDAG,
    TaskDispatchEnvelope,
    TaskNode,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import PersistenceEvidenceKind
from app.persistence.dag import PersistedDAGSnapshot, PersistedDAGSource
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import PersistedRunStatus
from app.runtime import EvidenceBoundTaskExecutionBaseResolver
from app.workers.executor import QueuedTaskWorker
from app.workspace import LocalGitWorkspace


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Execute {task_id} from an evidence-bound DAG base.",
        readable_files=["**/*"],
        writable_files=[f"{task_id.lower()}.txt"],
        readonly_files=[],
        acceptance_criteria=["Execution starts from the accepted dependency state."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("A"), depends_on=()),
            TaskNode(task=_task("B"), depends_on=("A",)),
        )
    )


def _success_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ],
    )


class _RecordingBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, **_kwargs):
        self.calls += 1
        raise AssertionError("backend must not run before execution-base authority is proven")


class _WorkerStore:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.appended = 0

    async def load_run(self, _run_id):
        return self.snapshot

    async def append_evidence(self, **_kwargs):
        self.appended += 1
        return self.appended

    async def finalize_single_task_run(self, **_kwargs):
        raise AssertionError("multi-task boundary test must not finalize a Run")


def test_multi_task_worker_fails_closed_without_execution_base_resolver() -> None:
    run_id = uuid4()
    task_a = _task("A")
    snapshot = SimpleNamespace(
        status=PersistedRunStatus.RUNNING,
        run_id=run_id,
        project_id=uuid4(),
        base_commit="a" * 40,
        tasks=(
            SimpleNamespace(task=task_a),
            SimpleNamespace(task=_task("B")),
        ),
    )
    store = _WorkerStore(snapshot)
    backend = _RecordingBackend()
    worker = QueuedTaskWorker(store=store, backend=backend)
    envelope = TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=run_id,
        task_id="A",
    )

    with pytest.raises(WorkerExecutionBoundaryError, match="execution-base resolver"):
        asyncio.run(worker.execute(envelope, run_token=uuid4()))

    assert backend.calls == 0
    # The boundary error is re-raised to fail closed, while the worker still leaves a durable
    # terminal execution record and a completed dispatch event before the lease is released.
    assert store.appended == 2


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _integration_repository(tmp_path: Path) -> tuple[LocalGitWorkspace, str, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "DevFlow Test")
    _git(root, "config", "user.email", "devflow@example.test")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    base_commit = _commit(root, "base")

    _git(root, "switch", "-c", "task-a")
    (root / "a.txt").write_text("task A\n", encoding="utf-8")
    task_commit = _commit(root, "task A")

    _git(root, "switch", "-c", "integration", base_commit)
    _git(root, "merge", "--no-ff", "task-a", "-m", "integrate A")
    integration_commit = _git(root, "rev-parse", "HEAD")
    assert _git(root, "rev-list", "--parents", "-n", "1", integration_commit).split()[1:] == [
        base_commit,
        task_commit,
    ]
    return LocalGitWorkspace(root), base_commit, task_commit, integration_commit


class _DAGReader:
    def __init__(self, snapshot: PersistedDAGSnapshot) -> None:
        self._snapshot = snapshot

    async def load_dag(self, _run_id: UUID) -> PersistedDAGSnapshot:
        return self._snapshot


class _WorkspaceResolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self._workspace = workspace

    def resolve(self, _project_id: UUID) -> LocalGitWorkspace:
        return self._workspace


def _resolver_snapshot(
    *,
    run_id: UUID,
    project_id: UUID,
    base_commit: str,
    task_commit: str,
    integration_commit: str,
):
    execution = WorkerExecutionEvidence(
        dispatch_id=uuid4(),
        run_id=run_id,
        task_id="A",
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base_commit,
        branch_name="task-a",
        commit_sha=task_commit,
        run_result=_success_result("A"),
        duration_ms=1,
    )
    merge_snapshot = MergeQueueSnapshot(
        integration_ref=f"refs/devflow/integration/{run_id.hex}",
        run_base_commit=base_commit,
        head_commit=integration_commit,
        integrated_task_ids=("A",),
        attempts=(
            MergeQueueAttempt(
                sequence=0,
                task_id="A",
                task_branch="task-a",
                task_base_commit=base_commit,
                task_commit=task_commit,
                previous_integration_commit=base_commit,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=integration_commit,
            ),
        ),
    )
    evidence = (
        SimpleNamespace(
            id=1,
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            task_id="A",
            payload=execution.model_dump(mode="json"),
            payload_sha256="1" * 64,
        ),
        SimpleNamespace(
            id=2,
            kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
            task_id=None,
            payload=merge_snapshot.model_dump(mode="json"),
            payload_sha256="2" * 64,
        ),
    )
    return SimpleNamespace(
        run_id=run_id,
        project_id=project_id,
        base_commit=base_commit,
        evidence=evidence,
    )


def _resolver(
    *,
    run_id: UUID,
    workspace: LocalGitWorkspace,
) -> EvidenceBoundTaskExecutionBaseResolver:
    dag_snapshot = PersistedDAGSnapshot(
        run_id=run_id,
        dag=_dag(),
        dag_sha256="d" * 64,
        source=PersistedDAGSource.PERSISTED,
    )
    return EvidenceBoundTaskExecutionBaseResolver(
        dag_reader=_DAGReader(dag_snapshot),
        workspace_resolver=_WorkspaceResolver(workspace),
    )


def test_dependent_execution_base_is_reproduced_from_real_two_parent_merge(
    tmp_path: Path,
) -> None:
    workspace, base_commit, task_commit, integration_commit = _integration_repository(tmp_path)
    run_id = uuid4()
    project_id = uuid4()
    snapshot = _resolver_snapshot(
        run_id=run_id,
        project_id=project_id,
        base_commit=base_commit,
        task_commit=task_commit,
        integration_commit=integration_commit,
    )
    resolver = _resolver(run_id=run_id, workspace=workspace)

    resolved = asyncio.run(resolver.resolve(snapshot=snapshot, task_id="B"))

    assert resolved.commit_sha == integration_commit
    assert resolved.source_evidence_id == 2
    assert resolved.integration_ref == f"refs/devflow/integration/{run_id.hex}"


def test_dependent_execution_base_rejects_fake_integration_parentage(tmp_path: Path) -> None:
    workspace, base_commit, task_commit, _integration_commit = _integration_repository(tmp_path)
    run_id = uuid4()
    project_id = uuid4()
    snapshot = _resolver_snapshot(
        run_id=run_id,
        project_id=project_id,
        base_commit=base_commit,
        task_commit=task_commit,
        integration_commit=task_commit,
    )
    resolver = _resolver(run_id=run_id, workspace=workspace)

    with pytest.raises(PersistenceCorruptionError, match="parent evidence"):
        asyncio.run(resolver.resolve(snapshot=snapshot, task_id="B"))
