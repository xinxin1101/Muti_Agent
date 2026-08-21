from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.agents import DeveloperAgent
from app.models import (
    AgentResponse,
    MergeAttemptOutcome,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDAG,
    TaskNode,
    TaskRunState,
    TaskScheduleState,
    TokenUsage,
    ToolCall,
    VerificationResult,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
    WorkerTaskResult,
)
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.persistence.errors import PersistenceConflictError
from app.persistence.serialization import canonical_payload
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_gate import IntegrationHumanGate
from app.runtime.integration_repair import IntegrationConflictRepairService
from app.runtime.repair_merge_queue import RepairAwareTopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler
from app.workspace import LocalGitWorkspace, TaskWorktreeManager


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


def _repository(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    (root / "shared.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return LocalGitWorkspace(root)


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Apply {task_id} change to shared.py.",
        readable_files=["shared.py"],
        writable_files=["shared.py"],
        readonly_files=[],
        acceptance_criteria=[f"{task_id} behavior remains present"],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _run_result(task: TaskContract) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=["shared.py"],
    )


def _finish_task(
    manager: TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: TaskContract,
    value: str,
) -> WorkerTaskResult:
    record = manager.create(task.task_id)
    task_workspace = manager.open_workspace(task.task_id)
    task_workspace.resolve_path("shared.py").write_text(value, encoding="utf-8")
    commit = manager.commit_task_changes(task.task_id)
    scheduler.start(task.task_id)
    scheduler.succeed(task.task_id)
    return WorkerTaskResult(
        task_id=task.task_id,
        scheduler_state=TaskScheduleState.SUCCEEDED,
        worktree_path=str(record.path),
        branch_name=record.branch_name,
        base_commit=record.base_commit,
        commit_sha=commit,
        run_result=_run_result(task),
        duration_ms=1,
    )


def _tool_call(path: str, content: str) -> ToolCall:
    return ToolCall(
        id="resolve-conflict",
        name="write_file",
        arguments=json.dumps({"path": path, "content": content}),
    )


def _response(*, tool_calls: list[ToolCall] | None = None) -> AgentResponse:
    return AgentResponse(
        model="test/developer",
        content="Resolved the classified conflict." if not tool_calls else "",
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


class FakeDriver:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("repair recovery unexpectedly called the Agent again")
        return self.responses.pop(0)


class PassingVerifier:
    def verify(self, task: TaskContract, *, workspace: LocalGitWorkspace) -> VerificationResult:
        del task, workspace
        return VerificationResult(passed=True, checks=[])


class FakeRepairWriter:
    def __init__(self) -> None:
        self.repairs: list[IntegrationConflictRepairEvidence] = []

    async def record_integration_repair(
        self,
        evidence: IntegrationConflictRepairEvidence,
    ) -> tuple[int, str]:
        if self.repairs and self.repairs[0] != evidence:
            raise AssertionError("repair idempotency key changed across restart")
        if not self.repairs:
            self.repairs.append(evidence)
        _, digest = canonical_payload(evidence)
        return 100, digest


class FakeEvidenceStore:
    def __init__(
        self,
        *,
        run_id: UUID,
        project_id: UUID,
        base_commit: str,
        task: TaskContract,
        worker: WorkerExecutionEvidence,
        writer: FakeRepairWriter,
    ) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.base_commit = base_commit
        self.task = task
        self.worker = worker
        self.writer = writer

    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot:
        assert run_id == self.run_id
        now = datetime.now(UTC)
        task_payload, task_digest = canonical_payload(self.task)
        worker_payload, worker_digest = canonical_payload(self.worker)
        evidence = [
            PersistedEvidence(
                id=1,
                run_id=run_id,
                task_id=self.task.task_id,
                evidence_key="worker:terminal",
                kind=PersistenceEvidenceKind.WORKER_EXECUTION,
                stage="worker",
                sequence=0,
                schema_version=1,
                payload=worker_payload,
                payload_sha256=worker_digest,
                created_at=now,
            )
        ]
        for index, repair in enumerate(self.writer.repairs, start=2):
            payload, digest = canonical_payload(repair)
            evidence.append(
                PersistedEvidence(
                    id=index,
                    run_id=run_id,
                    task_id=self.task.task_id,
                    evidence_key=(
                        f"integration:repair:{repair.task_id}:"
                        f"{repair.conflict_evidence_fingerprint}"
                    ),
                    kind=PersistenceEvidenceKind.INTEGRATION_REPAIR,
                    stage="integration_repair",
                    schema_version=1,
                    payload=payload,
                    payload_sha256=digest,
                    created_at=now,
                )
            )
        return PersistedRunSnapshot(
            run_id=run_id,
            project_id=self.project_id,
            repository_url="https://example.invalid/repo.git",
            default_branch="main",
            base_commit=self.base_commit,
            status=PersistedRunStatus.RUNNING,
            tasks=(
                PersistedTask(
                    task=self.task,
                    contract_sha256=task_digest,
                    created_at=now,
                ),
            ),
            evidence=tuple(evidence),
            started_at=now,
        )


class WorkspaceResolver:
    def __init__(self, project_id: UUID, workspace: LocalGitWorkspace) -> None:
        self.project_id = project_id
        self.workspace = workspace

    def resolve(self, project_id: UUID) -> LocalGitWorkspace:
        assert project_id == self.project_id
        return self.workspace


def test_persisted_repair_resumes_after_crash_before_git_cas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    dag = TaskDAG(
        tasks=(
            TaskNode(task=task_a, depends_on=()),
            TaskNode(task=task_b, depends_on=()),
        )
    )
    scheduler = DAGScheduler(dag)
    manager = TaskWorktreeManager(base, tmp_path / "worktrees")
    result_a = _finish_task(manager, scheduler, task_a, "VALUE = 'task-a'\n")
    result_b = _finish_task(manager, scheduler, task_b, "VALUE = 'task-b'\n")
    integration_id = "repair-recovery"
    queue = RepairAwareTopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id=integration_id,
    )
    stopped = queue.integrate([result_a, result_b])
    assert stopped.stopped is True

    conflict = GitMergeConflictClassifier(base).classify(stopped)
    gate = IntegrationHumanGate(
        workspace=base,
        queue_snapshot=stopped,
        scheduler=scheduler,
        evidence=conflict,
    ).record_human_decision(
        decision="AUTHORIZE_REPAIR",
        actor="maintainer",
        note="Bounded repair approved",
    )
    assert gate.human_decision is not None

    run_id = uuid4()
    project_id = uuid4()
    worker = WorkerExecutionEvidence(
        dispatch_id=uuid4(),
        run_id=run_id,
        task_id=task_b.task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=result_b.base_commit or "",
        branch_name=result_b.branch_name,
        commit_sha=result_b.commit_sha,
        run_result=result_b.run_result,
        duration_ms=1,
    )
    writer = FakeRepairWriter()
    evidence_store = FakeEvidenceStore(
        run_id=run_id,
        project_id=project_id,
        base_commit=manager.base_commit,
        task=task_b,
        worker=worker,
        writer=writer,
    )
    resolver = WorkspaceResolver(project_id, base)
    first_driver = FakeDriver(
        [
            _response(tool_calls=[_tool_call("shared.py", "VALUE = 'resolved'\n")]),
            _response(),
        ]
    )
    first = IntegrationConflictRepairService(
        evidence_store=evidence_store,
        repair_store=writer,
        workspace_resolver=resolver,
        developer=DeveloperAgent(driver=first_driver, model="test/developer"),
        verifier=PassingVerifier(),  # type: ignore[arg-type]
        repair_root=tmp_path / "repairs",
    )
    integration_before = _git(base.root, "rev-parse", stopped.integration_ref)

    def crash_before_cas(*args, **kwargs) -> None:
        del args, kwargs
        raise PersistenceConflictError("simulated crash before Git CAS")

    monkeypatch.setattr(first, "_advance_ref", crash_before_cas)
    with pytest.raises(PersistenceConflictError, match="simulated crash"):
        asyncio.run(first.repair(run_id=run_id, gate=gate, queue_snapshot=stopped))

    assert len(writer.repairs) == 1
    persisted_repair = writer.repairs[0]
    assert _git(base.root, "rev-parse", stopped.integration_ref) == integration_before
    assert len(first_driver.requests) == 2

    second_driver = FakeDriver([])
    second = IntegrationConflictRepairService(
        evidence_store=evidence_store,
        repair_store=writer,
        workspace_resolver=resolver,
        developer=DeveloperAgent(driver=second_driver, model="test/developer"),
        verifier=PassingVerifier(),  # type: ignore[arg-type]
        repair_root=tmp_path / "repairs",
    )
    resumed = asyncio.run(second.repair(run_id=run_id, gate=gate, queue_snapshot=stopped))

    assert resumed == persisted_repair
    assert second_driver.requests == []
    assert _git(base.root, "rev-parse", stopped.integration_ref) == persisted_repair.repair_commit
    assert _git(
        base.root,
        "show-ref",
        "--verify",
        "--quiet",
        conflict.conflict_ref,
        check=False,
    ) == ""
    assert _git(
        base.root,
        "show-ref",
        "--verify",
        "--quiet",
        gate.human_decision.decision_ref,
        check=False,
    ) == ""

    recovered = RepairAwareTopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=base,
        integration_id=integration_id,
    ).snapshot()
    assert recovered.stopped is False
    assert recovered.integrated_task_ids == (task_a.task_id, task_b.task_id)
    assert [attempt.outcome for attempt in recovered.attempts] == [
        MergeAttemptOutcome.INTEGRATED,
        MergeAttemptOutcome.CONFLICT,
        MergeAttemptOutcome.REPAIRED,
    ]
    assert recovered.head_commit == persisted_repair.repair_commit
