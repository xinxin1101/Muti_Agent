from __future__ import annotations

import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dramatiq import Worker
from dramatiq.brokers.stub import StubBroker
from pydantic import ValidationError

from app.dispatch import DramatiqTaskDispatcher, TaskDispatchRejectedError
from app.models import (
    CheckpointReason,
    ContextContinuationState,
    DeveloperRunResult,
    DeveloperStopReason,
    FailureReport,
    FailureSource,
    FailureType,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDispatchEnvelope,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.checkpoint import TaskCheckpoint
from app.persistence import PersistenceEvidenceKind
from app.persistence.types import PersistedRunStatus
from app.workers.actor import ACTOR_NAME, create_task_actor
from app.workers.executor import LocalQueuedTaskExecutionBackend, QueuedTaskWorker
from app.workspace import LocalGitWorkspace


def _task(task_id: str = "QUEUE-1") -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective="Update module.py.",
        readable_files=["module.py"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["module.py contains the requested value."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _success_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Verified."),
        ],
        changed_files=["module.py"],
    )


class _FakeRunReader:
    def __init__(self, task: TaskContract, *, status: PersistedRunStatus) -> None:
        self._snapshot = SimpleNamespace(
            status=status,
            tasks=(SimpleNamespace(task=task),),
        )

    async def load_run(self, _run_id):
        return self._snapshot


async def _noop_handler(_envelope: TaskDispatchEnvelope):
    raise AssertionError("dispatcher unit test must not execute the worker handler")


def test_dispatch_envelope_is_minimal_and_forbids_extra_fields() -> None:
    envelope = TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=uuid4(),
        task_id="TASK-1",
    )
    assert set(envelope.model_dump()) == {"dispatch_id", "run_id", "task_id"}

    with pytest.raises(ValidationError):
        TaskDispatchEnvelope.model_validate(
            {
                **envelope.model_dump(mode="json"),
                "run_token": str(uuid4()),
            }
        )


def test_dispatcher_enqueues_valid_persisted_identity_without_executing_it() -> None:
    task = _task()
    broker = StubBroker()
    actor = create_task_actor(
        broker=broker,
        handler=_noop_handler,
        queue_name="devflow_tasks",
    )
    run_id = uuid4()
    dispatcher = DramatiqTaskDispatcher(
        store=_FakeRunReader(task, status=PersistedRunStatus.RUNNING),
        actor=actor,
    )

    receipt = asyncio.run(dispatcher.dispatch(run_id=run_id, task_id=task.task_id))

    assert receipt.run_id == run_id
    assert receipt.task_id == task.task_id
    assert receipt.queue_name == "devflow_tasks"
    assert receipt.broker_message_id
    assert actor.actor_name == ACTOR_NAME
    assert actor.options["max_retries"] == 0
    assert actor.options["time_limit"] == 7_200_000


def test_actor_accepts_an_outer_emergency_time_limit() -> None:
    broker = StubBroker()
    actor = create_task_actor(
        broker=broker,
        handler=_noop_handler,
        queue_name="devflow_tasks",
        task_time_limit_seconds=1_800.5,
    )

    assert actor.options["time_limit"] == 1_800_500


def test_dispatcher_rejects_terminal_run_and_unknown_task() -> None:
    task = _task()
    broker = StubBroker()
    actor = create_task_actor(
        broker=broker,
        handler=_noop_handler,
        queue_name="devflow_tasks",
    )

    terminal = DramatiqTaskDispatcher(
        store=_FakeRunReader(task, status=PersistedRunStatus.SUCCEEDED),
        actor=actor,
    )
    with pytest.raises(TaskDispatchRejectedError, match="RUNNING"):
        asyncio.run(terminal.dispatch(run_id=uuid4(), task_id=task.task_id))

    running = DramatiqTaskDispatcher(
        store=_FakeRunReader(task, status=PersistedRunStatus.RUNNING),
        actor=actor,
    )
    with pytest.raises(TaskDispatchRejectedError, match="does not belong"):
        asyncio.run(running.dispatch(run_id=uuid4(), task_id="OTHER"))


def test_actor_executes_typed_envelope_and_disables_automatic_retries() -> None:
    broker = StubBroker()
    seen: list[TaskDispatchEnvelope] = []

    async def handler(envelope: TaskDispatchEnvelope):
        seen.append(envelope)
        return None

    actor = create_task_actor(
        broker=broker,
        handler=handler,
        queue_name="devflow_tasks",
    )
    worker = Worker(
        broker,
        queues={actor.queue_name},
        worker_timeout=50,
        worker_threads=1,
    )
    worker.start()
    try:
        envelope = TaskDispatchEnvelope(
            dispatch_id=uuid4(),
            run_id=uuid4(),
            task_id="TASK-ACTOR",
        )
        actor.send(envelope.model_dump(mode="json"))
        broker.join(actor.queue_name, timeout=2_000)
        worker.join()
    finally:
        worker.stop(timeout=2_000)

    assert seen == [envelope]
    assert actor.options["max_retries"] == 0


class _StaticWorkspaceResolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self._workspace = workspace

    def resolve(self, _project_id):
        return self._workspace


class _AllowGitFence:
    def __init__(self) -> None:
        self.tokens: list[UUID] = []

    @asynccontextmanager
    async def guard_task_git_mutation(
        self,
        *,
        run_id,
        task_id,
        dispatch_id,
        run_token,
    ):
        self.tokens.append(run_token)
        yield SimpleNamespace(
            run_id=run_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
        )


class _WritingRunner:
    def __init__(self, value: int) -> None:
        self._value = value

    async def run(self, task: TaskContract, *, workspace: LocalGitWorkspace):
        workspace.resolve_path("module.py").write_text(
            f"VALUE = {self._value}\n",
            encoding="utf-8",
        )
        return _success_result(task.task_id)


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repository(path: Path) -> LocalGitWorkspace:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "devflow@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "DevFlow Test"],
        check=True,
    )
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return LocalGitWorkspace(path)


def _backend(
    base: LocalGitWorkspace,
    worktree_root: Path,
    *,
    value: int = 2,
    fence: _AllowGitFence | None = None,
) -> tuple[LocalQueuedTaskExecutionBackend, _AllowGitFence]:
    git_fence = fence or _AllowGitFence()
    return (
        LocalQueuedTaskExecutionBackend(
            workspace_resolver=_StaticWorkspaceResolver(base),
            worktree_root=worktree_root,
            runner_factory=lambda _task: _WritingRunner(value),
            git_fence=git_fence,
        ),
        git_fence,
    )


def test_local_queued_backend_uses_isolated_worktree_and_fenced_commit(tmp_path: Path) -> None:
    base = _init_repository(tmp_path / "repo")
    task = _task("QUEUE-GIT")
    backend, fence = _backend(base, tmp_path / "worktrees")
    run_token = uuid4()

    evidence = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=uuid4(),
            dispatch_id=uuid4(),
            run_token=run_token,
            base_commit=base.head_commit(),
        )
    )

    assert evidence.status is WorkerExecutionStatus.SUCCEEDED
    assert evidence.commit_sha is not None
    assert evidence.branch_name is not None
    assert fence.tokens == [run_token, run_token]
    assert (base.root / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(base.root, "show", f"{evidence.commit_sha}:module.py") == "VALUE = 2"


def test_same_run_task_different_generations_use_distinct_worktree_identity(tmp_path: Path) -> None:
    base = _init_repository(tmp_path / "repo")
    task = _task("QUEUE-TAKEOVER")
    run_id = uuid4()
    backend, fence = _backend(base, tmp_path / "worktrees")
    first_token = uuid4()
    second_token = uuid4()

    first = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=run_id,
            dispatch_id=uuid4(),
            run_token=first_token,
            base_commit=base.head_commit(),
        )
    )
    second = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=run_id,
            dispatch_id=uuid4(),
            run_token=second_token,
            base_commit=base.head_commit(),
        )
    )

    assert first.status is WorkerExecutionStatus.SUCCEEDED
    assert second.status is WorkerExecutionStatus.SUCCEEDED
    assert first.branch_name != second.branch_name
    assert first.commit_sha is not None
    assert second.commit_sha is not None
    assert fence.tokens == [first_token, first_token, second_token, second_token]


def test_local_queued_backend_scopes_same_task_identity_to_each_run(tmp_path: Path) -> None:
    base = _init_repository(tmp_path / "repo")
    task = _task("QUEUE-REUSED-ID")
    backend, _ = _backend(base, tmp_path / "worktrees")
    base_commit = base.head_commit()

    first = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=uuid4(),
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=base_commit,
        )
    )
    second = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=uuid4(),
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=base_commit,
        )
    )

    assert first.status is WorkerExecutionStatus.SUCCEEDED
    assert second.status is WorkerExecutionStatus.SUCCEEDED
    assert first.branch_name != second.branch_name
    assert first.commit_sha is not None
    assert second.commit_sha is not None
    assert _git(base.root, "show", f"{first.commit_sha}:module.py") == "VALUE = 2"
    assert _git(base.root, "show", f"{second.commit_sha}:module.py") == "VALUE = 2"


def test_local_queued_backend_uses_persisted_base_after_managed_head_advances(
    tmp_path: Path,
) -> None:
    base = _init_repository(tmp_path / "repo")
    task = _task("QUEUE-FROZEN-BASE")
    persisted_base = base.head_commit()

    (base.root / "later.txt").write_text("created after run start\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(base.root), "add", "later.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(base.root), "commit", "-m", "advance managed head"],
        check=True,
        capture_output=True,
    )
    assert base.head_commit() != persisted_base

    backend, _ = _backend(base, tmp_path / "worktrees")
    evidence = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=uuid4(),
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=persisted_base,
        )
    )

    assert evidence.status is WorkerExecutionStatus.SUCCEEDED
    assert evidence.commit_sha is not None
    parents = _git(base.root, "rev-list", "--parents", "-n", "1", evidence.commit_sha).split()
    assert parents[1:] == [persisted_base]
    assert (
        "later.txt"
        not in _git(
            base.root,
            "ls-tree",
            "--name-only",
            evidence.commit_sha,
        ).splitlines()
    )
    assert (base.root / "later.txt").read_text(encoding="utf-8") == "created after run start\n"


class _RecordingStore:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot
        self.finalized: list[tuple[SingleTaskRunResult, UUID | None]] = []
        self.appended: list[tuple[PersistenceEvidenceKind, str, UUID | None]] = []

    async def load_run(self, _run_id):
        return self.snapshot

    async def append_evidence(
        self,
        *,
        run_id,
        evidence_key,
        kind,
        payload_model,
        task_id=None,
        stage=None,
        sequence=None,
        run_token=None,
    ):
        self.appended.append((kind, evidence_key, run_token))
        return len(self.appended)

    async def finalize_single_task_run(self, *, run_id, result, run_token=None):
        self.finalized.append((result, run_token))


class _CommitFailureBackend:
    def __init__(self, result: SingleTaskRunResult) -> None:
        self.result = result
        self.seen_token: UUID | None = None

    async def execute(
        self,
        *,
        task,
        project_id,
        run_id,
        dispatch_id,
        run_token,
        base_commit,
    ):
        self.seen_token = run_token
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.FAILED,
            base_commit=base_commit,
            branch_name="devflow/task/commit-failure",
            commit_sha=None,
            run_result=self.result,
            failures=(
                FailureReport(
                    failure_type=FailureType.TOOL_FAILURE,
                    source=FailureSource.RUNTIME,
                    message="Git commit failed after runtime success.",
                    retryable=False,
                    evidence=["commit_failed=true"],
                ),
            ),
            duration_ms=1,
        )


class _ExplodingBackend:
    async def execute(self, **_kwargs):
        raise RuntimeError("simulated worker boundary failure")


class _SuccessfulBackend:
    async def execute(self, *, base_commit, dispatch_id, run_id, task, **_kwargs):
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.SUCCEEDED,
            base_commit=base_commit,
            branch_name="devflow/task/success",
            commit_sha="b" * 40,
            run_result=_success_result(task.task_id),
            duration_ms=1,
        )


class _ExplodingBudgetManager:
    async def reclaim_unused_task_budget(self, **_kwargs) -> None:
        raise RuntimeError("simulated budget finalization failure")


def _time_limited_result(task_id: str) -> SingleTaskRunResult:
    failure = FailureReport(
        failure_type=FailureType.AGENT_TIME_LIMIT,
        source=FailureSource.RUNTIME,
        message="开发智能体时间预算耗尽。",
        retryable=False,
    )
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.FAILED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.FAILED, detail="Time budget exhausted."),
        ],
        developer=DeveloperRunResult(
            stop_reason=DeveloperStopReason.TIME_LIMIT,
            iterations=1,
            tool_calls=1,
            changed_files=["module.py"],
        ),
        failures=[failure],
        changed_files=["module.py"],
    )


class _ContinuationBackend:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self.bases: list[str] = []
        self.contexts = []

    async def execute(self, *, base_commit, dispatch_id, run_id, task, **_kwargs):
        self.bases.append(base_commit)
        self.contexts.append(_kwargs.get("continuation_context"))
        if len(self.bases) == 1:
            checkpoint_commit = "b" * 40
            return WorkerExecutionEvidence(
                dispatch_id=dispatch_id,
                run_id=run_id,
                task_id=task.task_id,
                status=WorkerExecutionStatus.FAILED,
                base_commit=base_commit,
                checkpoint=TaskCheckpoint(
                    task_id=task.task_id,
                    base_commit=base_commit,
                    commit_sha=checkpoint_commit,
                    changed_files=("module.py",),
                    reason=CheckpointReason.TIME_LIMIT,
                    summary="partial change saved",
                    context_state=ContextContinuationState(
                        summary_version="repository_summary_v1",
                        repository_head=base_commit,
                        changed_files=("module.py",),
                        remaining_summary="continue implementation",
                    ),
                ),
                run_result=_time_limited_result(task.task_id),
                failures=tuple(_time_limited_result(task.task_id).failures),
                duration_ms=1,
            )
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.SUCCEEDED,
            base_commit=base_commit,
            branch_name="devflow/task/continued",
            commit_sha="c" * 40,
            run_result=_success_result(task.task_id),
            duration_ms=1,
        )


def test_worker_propagates_token_and_does_not_finalize_success_when_git_commit_failed() -> None:
    task = _task("QUEUE-COMMIT-FAIL")
    run_id = uuid4()
    snapshot = SimpleNamespace(
        status=PersistedRunStatus.RUNNING,
        run_id=run_id,
        project_id=uuid4(),
        base_commit="a" * 40,
        tasks=(SimpleNamespace(task=task),),
    )
    store = _RecordingStore(snapshot)
    backend = _CommitFailureBackend(_success_result(task.task_id))
    worker = QueuedTaskWorker(store=store, backend=backend)
    envelope = TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=run_id,
        task_id=task.task_id,
    )
    run_token = uuid4()

    evidence = asyncio.run(worker.execute(envelope, run_token=run_token))

    assert evidence.status is WorkerExecutionStatus.FAILED
    assert backend.seen_token == run_token
    assert store.finalized == []
    assert store.appended
    assert all(item[2] == run_token for item in store.appended)
    assert any(item[0] is PersistenceEvidenceKind.WORKER_EXECUTION for item in store.appended)


def test_worker_persists_terminal_failure_before_returning_an_unhandled_boundary_error() -> None:
    task = _task("QUEUE-TERMINAL-GUARD")
    run_id = uuid4()
    snapshot = SimpleNamespace(
        status=PersistedRunStatus.RUNNING,
        run_id=run_id,
        project_id=uuid4(),
        base_commit="a" * 40,
        tasks=(SimpleNamespace(task=task),),
    )
    store = _RecordingStore(snapshot)
    worker = QueuedTaskWorker(store=store, backend=_ExplodingBackend())
    envelope = TaskDispatchEnvelope(dispatch_id=uuid4(), run_id=run_id, task_id=task.task_id)

    evidence = asyncio.run(worker.execute(envelope, run_token=uuid4()))

    assert evidence.status is WorkerExecutionStatus.FAILED
    assert evidence.failures[0].evidence[0] == "terminalization=worker_exception"
    assert any(item[0] is PersistenceEvidenceKind.WORKER_EXECUTION for item in store.appended)


def test_worker_keeps_execution_evidence_and_settles_dispatch_when_budget_cleanup_fails() -> None:
    task = _task("QUEUE-BUDGET-FINALIZATION")
    run_id = uuid4()
    snapshot = SimpleNamespace(
        status=PersistedRunStatus.RUNNING,
        run_id=run_id,
        project_id=uuid4(),
        base_commit="a" * 40,
        tasks=(SimpleNamespace(task=task),),
    )
    store = _RecordingStore(snapshot)
    worker = QueuedTaskWorker(
        store=store,
        backend=_SuccessfulBackend(),
        token_budget_manager=_ExplodingBudgetManager(),
    )
    envelope = TaskDispatchEnvelope(dispatch_id=uuid4(), run_id=run_id, task_id=task.task_id)

    evidence = asyncio.run(worker.execute(envelope, run_token=uuid4()))

    assert evidence.status is WorkerExecutionStatus.SUCCEEDED
    execution_keys = [
        key
        for kind, key, _token in store.appended
        if kind is PersistenceEvidenceKind.WORKER_EXECUTION
    ]
    assert execution_keys == [f"dispatch:{envelope.dispatch_id}:execution"]
    assert (
        PersistenceEvidenceKind.FAILURE_REPORT,
        f"dispatch:{envelope.dispatch_id}:post-execution-finalization-failure",
    ) in [(kind, key) for kind, key, _token in store.appended]
    assert (
        PersistenceEvidenceKind.DISPATCH_EVENT,
        f"dispatch:{envelope.dispatch_id}:completed",
    ) in [(kind, key) for kind, key, _token in store.appended]


def test_worker_automatically_continues_time_limited_checkpoint_from_its_commit() -> None:
    task = _task("QUEUE-CONTINUE")
    run_id = uuid4()
    snapshot = SimpleNamespace(
        status=PersistedRunStatus.RUNNING,
        run_id=run_id,
        project_id=uuid4(),
        base_commit="a" * 40,
        tasks=(SimpleNamespace(task=task),),
    )
    store = _RecordingStore(snapshot)
    backend = _ContinuationBackend(task.task_id)
    worker = QueuedTaskWorker(store=store, backend=backend, continuation_max_slices=3)
    envelope = TaskDispatchEnvelope(dispatch_id=uuid4(), run_id=run_id, task_id=task.task_id)

    evidence = asyncio.run(worker.execute(envelope, run_token=uuid4()))

    assert backend.bases == ["a" * 40, "b" * 40]
    assert backend.contexts[0] is None
    assert backend.contexts[1] is not None
    assert backend.contexts[1].changed_files == ("module.py",)
    assert evidence.status is WorkerExecutionStatus.SUCCEEDED
    assert evidence.continuation is not None
    assert evidence.continuation.slices_started == 2
    assert any("checkpoint:slice:0001" in item[1] for item in store.appended)
    assert store.finalized and store.finalized[0][0].status is TaskRunState.SUCCEEDED
