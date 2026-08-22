from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dramatiq.errors import BrokerConnectionError

from app.dispatch import DurableDramatiqTaskDispatcher, TaskDispatchBrokerError
from app.models import (
    DAGTaskFrontierState,
    MergeAttemptOutcome,
    MergeQueueAttempt,
    MergeQueueSnapshot,
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDAG,
    TaskNode,
    TaskReconciliationAction,
    TaskRunState,
    TaskScheduleState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
    WorkerTaskResult,
)
from app.models.dispatch_attempt import DispatchAttemptState
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
    StaleRunTokenError,
)
from app.runtime import (
    DAGRunReconciler,
    EvidenceBoundTaskExecutionBaseResolver,
    IdempotentTaskReconciler,
)
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.durable_human_gate import DurableHumanGateService
from app.runtime.repair_merge_queue import RepairAwareTopologicalMergeQueue
from app.runtime.scheduler import DAGScheduler
from app.workers.executor import LocalQueuedTaskExecutionBackend
from app.workspace import LocalGitWorkspace, TaskWorktreeManager

_RUN_BASE = "a" * 40
_TASK_A_COMMIT = "b" * 40
_INTEGRATION_A = "c" * 40


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for Step 5.8 chaos tests")
    pytest.skip("Step 5.8 chaos tests require DEVFLOW_DATABASE_URL")


def _task(task_id: str, *, writable_file: str | None = None) -> TaskContract:
    path = writable_file or f"src/{task_id.lower().replace('_', '-')}.py"
    return TaskContract(
        task_id=task_id,
        objective=f"Exercise durable recovery for {task_id}.",
        readable_files=["src/**", "shared.py", "module.py"],
        writable_files=[path],
        readonly_files=["tests/**"],
        acceptance_criteria=["Durable authority remains fail closed under injected failure."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


async def _new_run(store: PostgresEvidenceStore, task: TaskContract) -> UUID:
    project_id = await store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/chaos.git",
        default_branch="main",
    )
    return await store.start_run(
        project_id=project_id,
        tasks=(task,),
        base_commit=_RUN_BASE,
    )


class _AckActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def send(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        return SimpleNamespace(message_id=f"chaos-{self.calls}")


class _UnavailableActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0

    def send(self, _payload):
        self.calls += 1
        raise BrokerConnectionError("injected Step 5.8 broker failure")


class _StaticWorkspaceResolver:
    def __init__(self, project_id: UUID, workspace: LocalGitWorkspace) -> None:
        self._project_id = project_id
        self._workspace = workspace

    def resolve(self, project_id: UUID) -> LocalGitWorkspace:
        assert project_id == self._project_id
        return self._workspace


class _WritingRunner:
    def __init__(self, value: int) -> None:
        self._value = value

    async def run(self, task: TaskContract, *, workspace: LocalGitWorkspace) -> SingleTaskRunResult:
        workspace.resolve_path("module.py").write_text(
            f"VALUE = {self._value}\n",
            encoding="utf-8",
        )
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.SUCCEEDED,
            events=(
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Verified."),
            ),
            changed_files=("module.py",),
        )


def _success_result(task_id: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=(
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
        ),
    )


def _worker_execution(
    *,
    run_id: UUID,
    task_id: str,
    dispatch_id: UUID,
    base_commit: str = _RUN_BASE,
    commit_sha: str = _TASK_A_COMMIT,
    branch_name: str | None = None,
) -> WorkerExecutionEvidence:
    return WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base_commit,
        branch_name=branch_name or f"devflow/{task_id.lower()}-success",
        commit_sha=commit_sha,
        run_result=_success_result(task_id),
        duration_ms=10,
    )


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_module_repository(path: Path) -> LocalGitWorkspace:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "devflow@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "DevFlow Chaos"],
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


def _init_shared_repository(path: Path) -> LocalGitWorkspace:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "devflow@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "DevFlow Chaos"],
        check=True,
    )
    (path / "shared.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "shared.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return LocalGitWorkspace(path)


async def _record_terminal_execution(
    *,
    evidence_store: PostgresEvidenceStore,
    dispatch_store: PostgresDispatchAttemptStore,
    lease_store: PostgresTaskLeaseStore,
    run_id: UUID,
    task_id: str,
    base_commit: str = _RUN_BASE,
    commit_sha: str = _TASK_A_COMMIT,
    branch_name: str | None = None,
) -> tuple[UUID, int]:
    dispatch_id = uuid4()
    await dispatch_store.begin_initial_attempt(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
    )
    await dispatch_store.mark_enqueued(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        broker_message_id=f"terminal-{task_id}",
        queue_name="devflow_tasks",
    )
    grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task_id,
        owner_id=f"worker-{task_id}",
        dispatch_id=dispatch_id,
        lease_seconds=60,
    )
    evidence_id = await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task_id,
        evidence_key=f"chaos:{dispatch_id}:execution",
        kind=PersistenceEvidenceKind.WORKER_EXECUTION,
        payload_model=_worker_execution(
            run_id=run_id,
            task_id=task_id,
            dispatch_id=dispatch_id,
            base_commit=base_commit,
            commit_sha=commit_sha,
            branch_name=branch_name,
        ),
        stage="worker",
        run_token=grant.run_token,
    )
    await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task_id,
        owner_id=f"worker-{task_id}",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
    )
    return dispatch_id, evidence_id


def _run_reconciler(
    *,
    evidence_store: PostgresEvidenceStore,
    dag_store: PostgresDAGStore,
    lease_store: PostgresTaskLeaseStore,
    reconciliation_store: PostgresTaskReconciliationStore,
    actor: _AckActor,
) -> tuple[DAGRunReconciler, IdempotentTaskReconciler]:
    task_reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    resolver = EvidenceBoundTaskExecutionBaseResolver(dag_reader=dag_store)
    return (
        DAGRunReconciler(
            run_reader=evidence_store,
            dag_reader=dag_store,
            lease_reader=lease_store,
            task_reconciler=task_reconciler,
            execution_base_resolver=resolver,
        ),
        task_reconciler,
    )


def test_c01_active_worker_loss_waits_for_expiry() -> None:
    asyncio.run(_c01_active_worker_loss_waits_for_expiry())


async def _c01_active_worker_loss_waits_for_expiry() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("C01-ACTIVE")
        run_id = await _new_run(evidence_store, task)
        dispatch_id = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            broker_message_id="c01-active",
            queue_name="devflow_tasks",
        )
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="worker-that-disappears",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )

        outcome = await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert outcome.receipt is None
        assert outcome.decision.action is TaskReconciliationAction.WAIT_ACTIVE_OWNER
        assert actor.calls == 0
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
    finally:
        await reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_c02_stale_generation_cannot_append_evidence() -> None:
    asyncio.run(_c02_stale_generation_cannot_append_evidence())


async def _c02_stale_generation_cannot_append_evidence() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    try:
        task = _task("C02-FENCING")
        run_id = await _new_run(evidence_store, task)
        old_dispatch = uuid4()
        old_grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="old-worker",
            dispatch_id=old_dispatch,
            lease_seconds=0.05,
        )
        await asyncio.sleep(0.08)
        new_grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="new-worker",
            dispatch_id=uuid4(),
            lease_seconds=60,
        )
        assert new_grant.snapshot.generation == old_grant.snapshot.generation + 1

        event = RunEvent(
            sequence=0,
            state=TaskRunState.RUNNING,
            detail="Injected late write from expired generation.",
        )
        with pytest.raises(StaleRunTokenError):
            await evidence_store.append_evidence(
                run_id=run_id,
                task_id=task.task_id,
                evidence_key="chaos:c02:stale-write",
                kind=PersistenceEvidenceKind.STATE_TRANSITION,
                payload_model=event,
                stage="chaos",
                sequence=0,
                run_token=old_grant.run_token,
            )

        snapshot = await evidence_store.load_run(run_id)
        assert all(item.evidence_key != "chaos:c02:stale-write" for item in snapshot.evidence)
    finally:
        await lease_store.dispose()
        await evidence_store.dispose()


def test_c03_stale_generation_cannot_mutate_git(tmp_path: Path) -> None:
    asyncio.run(_c03_stale_generation_cannot_mutate_git(tmp_path))


async def _c03_stale_generation_cannot_mutate_git(tmp_path: Path) -> None:
    database_url = _database_url()
    workspace = _init_module_repository(tmp_path / "repo")
    task = _task("C03-GIT", writable_file="module.py")
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    try:
        project_id = await evidence_store.ensure_project(
            repository_url=f"https://example.test/{uuid4()}/chaos-git.git",
            default_branch="main",
        )
        base_commit = workspace.head_commit()
        run_id = await evidence_store.start_run(
            project_id=project_id,
            tasks=(task,),
            base_commit=base_commit,
        )
        old_dispatch = uuid4()
        old_grant = await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="old-git-worker",
            dispatch_id=old_dispatch,
            lease_seconds=0.05,
        )
        await asyncio.sleep(0.08)
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="new-git-worker",
            dispatch_id=uuid4(),
            lease_seconds=60,
        )

        refs_before = _git(workspace.root, "for-each-ref", "--format=%(refname)", "refs/heads")
        backend = LocalQueuedTaskExecutionBackend(
            workspace_resolver=_StaticWorkspaceResolver(project_id, workspace),
            worktree_root=tmp_path / "worktrees",
            runner_factory=lambda _task: _WritingRunner(2),
            git_fence=lease_store,
        )
        stale = await backend.execute(
            task=task,
            project_id=project_id,
            run_id=run_id,
            dispatch_id=old_dispatch,
            run_token=old_grant.run_token,
            base_commit=base_commit,
        )
        refs_after = _git(workspace.root, "for-each-ref", "--format=%(refname)", "refs/heads")

        assert stale.status is WorkerExecutionStatus.FAILED
        assert stale.commit_sha is None
        assert stale.branch_name is None
        assert refs_after == refs_before
    finally:
        await lease_store.dispose()
        await evidence_store.dispose()


def test_c04_terminal_evidence_resumes_without_semantic_rerun() -> None:
    asyncio.run(_c04_terminal_evidence_resumes_without_semantic_rerun())


async def _c04_terminal_evidence_resumes_without_semantic_rerun() -> None:
    database_url = _database_url()
    setup_evidence = PostgresEvidenceStore.from_url(database_url)
    setup_dispatch = PostgresDispatchAttemptStore.from_url(database_url)
    setup_lease = PostgresTaskLeaseStore.from_url(database_url)
    task = _task("C04-TERMINAL")
    run_id = await _new_run(setup_evidence, task)
    _, evidence_id = await _record_terminal_execution(
        evidence_store=setup_evidence,
        dispatch_store=setup_dispatch,
        lease_store=setup_lease,
        run_id=run_id,
        task_id=task.task_id,
    )
    await setup_lease.dispose()
    await setup_dispatch.dispose()
    await setup_evidence.dispose()

    evidence_store = PostgresEvidenceStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        outcome = await reconciler.reconcile(run_id=run_id, task_id=task.task_id)

        assert outcome.receipt is None
        assert outcome.decision.action is TaskReconciliationAction.RESUME_TERMINAL_EVIDENCE
        assert outcome.decision.terminal_worker_evidence_id == evidence_id
        assert actor.calls == 0
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert len(attempts) == 1
        snapshot = await evidence_store.load_run(run_id)
        assert any(item.id == evidence_id for item in snapshot.evidence)
    finally:
        await reconciler.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_c05_duplicate_reconcilers_publish_once() -> None:
    asyncio.run(_c05_duplicate_reconcilers_publish_once())


async def _c05_duplicate_reconcilers_publish_once() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler = IdempotentTaskReconciler(store=reconciliation_store, actor=actor)
    try:
        task = _task("C05-RACE")
        run_id = await _new_run(evidence_store, task)
        first_dispatch = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=first_dispatch,
            run_id=run_id,
            task_id=task.task_id,
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=first_dispatch,
            run_id=run_id,
            task_id=task.task_id,
            broker_message_id="c05-generation-1",
            queue_name="devflow_tasks",
        )
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id=task.task_id,
            owner_id="expired-worker",
            dispatch_id=first_dispatch,
            lease_seconds=0.05,
        )
        await asyncio.sleep(0.08)

        outcomes = await asyncio.gather(
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
            reconciler.reconcile(run_id=run_id, task_id=task.task_id),
        )

        assert actor.calls == 1
        assert sum(outcome.receipt is not None for outcome in outcomes) == 1
        attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task.task_id)
        assert [item.attempt_number for item in attempts] == [1, 2]
        assert attempts[-1].state is DispatchAttemptState.ENQUEUED
    finally:
        await reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await evidence_store.dispose()


def test_c06_publish_failure_remains_explicit() -> None:
    asyncio.run(_c06_publish_failure_remains_explicit())


async def _c06_publish_failure_remains_explicit() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    try:
        task = _task("C06-BROKER")
        run_id = await _new_run(evidence_store, task)
        actor = _UnavailableActor()
        dispatcher = DurableDramatiqTaskDispatcher(
            run_store=evidence_store,
            ledger=dispatch_store,
            actor=actor,
        )
        dispatch_id = uuid4()

        with pytest.raises(TaskDispatchBrokerError, match="broker could not accept"):
            await dispatcher.dispatch(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
            )

        attempt = await dispatch_store.load(dispatch_id)
        assert attempt is not None
        assert attempt.state is DispatchAttemptState.PUBLISH_FAILED
        assert actor.calls == 1
        snapshot = await evidence_store.load_run(run_id)
        assert snapshot.status.value == "RUNNING"
        assert not any(
            item.kind is PersistenceEvidenceKind.WORKER_EXECUTION
            for item in snapshot.evidence
        )
    finally:
        await dispatch_store.dispose()
        await evidence_store.dispose()


def _finish_conflicting_task(
    manager: TaskWorktreeManager,
    scheduler: DAGScheduler,
    task: TaskContract,
    value: str,
) -> WorkerTaskResult:
    record = manager.create(task.task_id)
    workspace = manager.open_workspace(task.task_id)
    workspace.resolve_path("shared.py").write_text(value, encoding="utf-8")
    commit_sha = manager.commit_task_changes(task.task_id)
    scheduler.start(task.task_id)
    scheduler.succeed(task.task_id)
    return WorkerTaskResult(
        task_id=task.task_id,
        scheduler_state=TaskScheduleState.SUCCEEDED,
        worktree_path=str(record.path),
        branch_name=record.branch_name,
        base_commit=record.base_commit,
        commit_sha=commit_sha,
        run_result=SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.SUCCEEDED,
            events=(
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
            ),
            changed_files=("shared.py",),
        ),
        duration_ms=1,
    )


def test_c07_pending_human_gate_survives_restart(tmp_path: Path) -> None:
    asyncio.run(_c07_pending_human_gate_survives_restart(tmp_path))


async def _c07_pending_human_gate_survives_restart(tmp_path: Path) -> None:
    database_url = _database_url()
    workspace = _init_shared_repository(tmp_path / "repo")
    task_a = _task("C07-A", writable_file="shared.py")
    task_b = _task("C07-B", writable_file="shared.py")
    dag = TaskDAG(
        tasks=(
            TaskNode(task=task_a, depends_on=()),
            TaskNode(task=task_b, depends_on=()),
        )
    )
    scheduler = DAGScheduler(dag)
    manager = TaskWorktreeManager(workspace, tmp_path / "worktrees")
    result_a = _finish_conflicting_task(manager, scheduler, task_a, "VALUE = 'a'\n")
    result_b = _finish_conflicting_task(manager, scheduler, task_b, "VALUE = 'b'\n")
    queue = RepairAwareTopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=manager,
        base_workspace=workspace,
        integration_id="chaos-c07",
    )
    stopped = queue.integrate((result_a, result_b))
    assert stopped.stopped is True
    conflict = GitMergeConflictClassifier(workspace).classify(stopped)

    first_evidence = PostgresEvidenceStore.from_url(database_url)
    first_dag = PostgresDAGStore.from_url(database_url)
    project_id = await first_evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/human-gate.git",
        default_branch="main",
    )
    run_id = await first_dag.start_run(
        project_id=project_id,
        dag=dag,
        base_commit=manager.base_commit,
    )
    resolver = _StaticWorkspaceResolver(project_id, workspace)
    first_service = DurableHumanGateService(
        evidence_store=first_evidence,
        dag_store=first_dag,
        workspace_resolver=resolver,
    )
    pending = await first_service.persist_live_gate(
        run_id=run_id,
        queue_snapshot=stopped,
        scheduler=scheduler,
        conflict=conflict,
        workspace=workspace,
    )
    assert pending.human_decision is None
    await first_dag.dispose()
    await first_evidence.dispose()

    second_evidence = PostgresEvidenceStore.from_url(database_url)
    second_dag = PostgresDAGStore.from_url(database_url)
    try:
        restarted = DurableHumanGateService(
            evidence_store=second_evidence,
            dag_store=second_dag,
            workspace_resolver=resolver,
        )
        gates = await restarted.list_gates(run_id)

        assert len(gates) == 1
        assert gates[0] == pending
        assert gates[0].human_decision is None
        assert gates[0].evidence_fingerprint == pending.evidence_fingerprint
    finally:
        await second_dag.dispose()
        await second_evidence.dispose()


def test_c08_dag_resume_does_not_rerun_completed_dependencies() -> None:
    asyncio.run(_c08_dag_resume_does_not_rerun_completed_dependencies())


async def _c08_dag_resume_does_not_rerun_completed_dependencies() -> None:
    database_url = _database_url()
    task_a = _task("A")
    task_b = _task("B")
    dag = TaskDAG(
        tasks=(
            TaskNode(task=task_a, depends_on=()),
            TaskNode(task=task_b, depends_on=("A",)),
        )
    )
    setup_evidence = PostgresEvidenceStore.from_url(database_url)
    setup_dag = PostgresDAGStore.from_url(database_url)
    setup_dispatch = PostgresDispatchAttemptStore.from_url(database_url)
    setup_lease = PostgresTaskLeaseStore.from_url(database_url)
    project_id = await setup_evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/dag-resume.git",
        default_branch="main",
    )
    run_id = await setup_dag.start_run(
        project_id=project_id,
        dag=dag,
        base_commit=_RUN_BASE,
    )
    await _record_terminal_execution(
        evidence_store=setup_evidence,
        dispatch_store=setup_dispatch,
        lease_store=setup_lease,
        run_id=run_id,
        task_id="A",
    )
    merge = MergeQueueSnapshot(
        integration_ref=f"refs/devflow/integration/{run_id.hex}",
        run_base_commit=_RUN_BASE,
        head_commit=_INTEGRATION_A,
        integrated_task_ids=("A",),
        attempts=(
            MergeQueueAttempt(
                sequence=0,
                task_id="A",
                task_branch="devflow/a-success",
                task_base_commit=_RUN_BASE,
                task_commit=_TASK_A_COMMIT,
                previous_integration_commit=_RUN_BASE,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=_INTEGRATION_A,
            ),
        ),
    )
    await setup_evidence.append_evidence(
        run_id=run_id,
        evidence_key="chaos:c08:merge-a",
        kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
        payload_model=merge,
        stage="integration",
    )
    await setup_lease.dispose()
    await setup_dispatch.dispose()
    await setup_dag.dispose()
    await setup_evidence.dispose()

    evidence_a = PostgresEvidenceStore.from_url(database_url)
    dag_a = PostgresDAGStore.from_url(database_url)
    lease_a = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_a = PostgresTaskReconciliationStore.from_url(database_url)
    dispatch_a = PostgresDispatchAttemptStore.from_url(database_url)
    actor_a = _AckActor()
    run_reconciler_a, task_reconciler_a = _run_reconciler(
        evidence_store=evidence_a,
        dag_store=dag_a,
        lease_store=lease_a,
        reconciliation_store=reconciliation_a,
        actor=actor_a,
    )
    try:
        first = await run_reconciler_a.reconcile_run(run_id)
        assert first.plan.completed_task_ids == ("A",)
        assert first.plan.reconcile_task_ids == ("B",)
        assert actor_a.calls == 1
        assert actor_a.payloads[0]["task_id"] == "B"
        a_attempts = await dispatch_a.list_for_task(run_id=run_id, task_id="A")
        b_attempts = await dispatch_a.list_for_task(run_id=run_id, task_id="B")
        assert len(a_attempts) == 1
        assert len(b_attempts) == 1
    finally:
        await task_reconciler_a.dispose()
        await dispatch_a.dispose()
        await lease_a.dispose()
        await dag_a.dispose()
        await evidence_a.dispose()

    evidence_b = PostgresEvidenceStore.from_url(database_url)
    dag_b = PostgresDAGStore.from_url(database_url)
    lease_b = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_b = PostgresTaskReconciliationStore.from_url(database_url)
    dispatch_b = PostgresDispatchAttemptStore.from_url(database_url)
    actor_b = _AckActor()
    run_reconciler_b, task_reconciler_b = _run_reconciler(
        evidence_store=evidence_b,
        dag_store=dag_b,
        lease_store=lease_b,
        reconciliation_store=reconciliation_b,
        actor=actor_b,
    )
    try:
        second = await run_reconciler_b.reconcile_run(run_id)
        assert second.plan.completed_task_ids == ("A",)
        assert actor_b.calls == 0
        task_b_plan = next(item for item in second.plan.tasks if item.task_id == "B")
        assert task_b_plan.frontier_state in {
            DAGTaskFrontierState.RECONCILE_CANDIDATE,
            DAGTaskFrontierState.WAIT_ACTIVE_OWNER,
        }
        a_attempts = await dispatch_b.list_for_task(run_id=run_id, task_id="A")
        b_attempts = await dispatch_b.list_for_task(run_id=run_id, task_id="B")
        assert len(a_attempts) == 1
        assert len(b_attempts) == 1
    finally:
        await task_reconciler_b.dispose()
        await dispatch_b.dispose()
        await lease_b.dispose()
        await dag_b.dispose()
        await evidence_b.dispose()
