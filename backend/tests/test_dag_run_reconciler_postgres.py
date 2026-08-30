from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models import (
    DAGTaskFrontierState,
    FailureReport,
    FailureSource,
    FailureType,
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
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.runtime import (
    DAGRunReconciler,
    EvidenceBoundTaskExecutionBaseResolver,
    IdempotentTaskReconciler,
)

_RUN_BASE = "a" * 40
_TASK_A_COMMIT = "b" * 40
_INTEGRATION_A = "c" * 40


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for DAG reconciliation tests")
    pytest.skip("PostgreSQL DAG reconciliation tests require DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Durably reconcile {task_id}.",
        readable_files=["src/**"],
        writable_files=[f"src/{task_id.lower()}.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["DAG recovery remains deterministic and evidence-bound."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _chain_dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("A"), depends_on=()),
            TaskNode(task=_task("B"), depends_on=("A",)),
        )
    )


def _parallel_dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("A"), depends_on=()),
            TaskNode(task=_task("B"), depends_on=()),
        )
    )


class _AckActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.calls = 0
        self.payloads: list[dict] = []

    def send(self, payload):
        self.calls += 1
        self.payloads.append(payload)
        return SimpleNamespace(message_id=f"dag-reconcile-{self.calls}")


async def _new_run(
    evidence_store: PostgresEvidenceStore,
    dag_store: PostgresDAGStore,
    dag: TaskDAG,
) -> UUID:
    project_id = await evidence_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/dag-reconcile.git",
        default_branch="main",
    )
    return await dag_store.start_run(
        project_id=project_id,
        dag=dag,
        base_commit=_RUN_BASE,
    )


def _terminal_execution(
    *,
    run_id: UUID,
    task_id: str,
    dispatch_id: UUID,
    status: WorkerExecutionStatus,
    base_commit: str = _RUN_BASE,
    commit_sha: str | None = None,
) -> WorkerExecutionEvidence:
    if status is WorkerExecutionStatus.SUCCEEDED:
        result = SingleTaskRunResult(
            task_id=task_id,
            status=TaskRunState.SUCCEEDED,
            events=[
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Completed."),
            ],
        )
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task_id,
            status=status,
            base_commit=base_commit,
            branch_name=f"devflow/{task_id.lower()}-success",
            commit_sha=commit_sha or _TASK_A_COMMIT,
            run_result=result,
            duration_ms=20,
        )

    failure = FailureReport(
        failure_type=FailureType.TEST_FAILURE,
        source=FailureSource.VERIFICATION,
        message="Deterministic verification failed.",
        retryable=False,
        evidence=["pytest_failed=true"],
    )
    result = SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.FAILED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.FAILED, detail="Failed."),
        ],
        failures=[failure],
    )
    return WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        status=status,
        base_commit=base_commit,
        branch_name=f"devflow/{task_id.lower()}-failed",
        commit_sha=None,
        run_result=result,
        failures=(failure,),
        duration_ms=20,
    )


async def _record_terminal(
    *,
    evidence_store: PostgresEvidenceStore,
    dispatch_store: PostgresDispatchAttemptStore,
    lease_store: PostgresTaskLeaseStore,
    run_id: UUID,
    task_id: str,
    status: WorkerExecutionStatus,
    base_commit: str = _RUN_BASE,
    commit_sha: str | None = None,
) -> WorkerExecutionEvidence:
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
    execution = _terminal_execution(
        run_id=run_id,
        task_id=task_id,
        dispatch_id=dispatch_id,
        status=status,
        base_commit=base_commit,
        commit_sha=commit_sha,
    )
    await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task_id,
        evidence_key=f"dag-reconcile:{dispatch_id}:execution",
        kind=PersistenceEvidenceKind.WORKER_EXECUTION,
        payload_model=execution,
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
    return execution


async def _append_integrated_a(
    *,
    evidence_store: PostgresEvidenceStore,
    run_id: UUID,
) -> None:
    snapshot = MergeQueueSnapshot(
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
    await evidence_store.append_evidence(
        run_id=run_id,
        evidence_key="dag-reconcile:merge:0000",
        kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
        payload_model=snapshot,
        stage="integration",
    )


def _run_reconciler(
    *,
    evidence_store: PostgresEvidenceStore,
    dag_store: PostgresDAGStore,
    lease_store: PostgresTaskLeaseStore,
    reconciliation_store: PostgresTaskReconciliationStore,
    actor: _AckActor,
) -> tuple[DAGRunReconciler, IdempotentTaskReconciler]:
    task_reconciler = IdempotentTaskReconciler(
        store=reconciliation_store,
        actor=actor,
    )
    base_resolver = EvidenceBoundTaskExecutionBaseResolver(dag_reader=dag_store)
    return (
        DAGRunReconciler(
            run_reader=evidence_store,
            dag_reader=dag_store,
            lease_reader=lease_store,
            task_reconciler=task_reconciler,
            execution_base_resolver=base_resolver,
        ),
        task_reconciler,
    )


def test_successful_dependency_waits_for_integration_base() -> None:
    asyncio.run(_successful_dependency_waits_for_integration_base())


async def _successful_dependency_waits_for_integration_base() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler, task_reconciler = _run_reconciler(
        evidence_store=evidence_store,
        dag_store=dag_store,
        lease_store=lease_store,
        reconciliation_store=reconciliation_store,
        actor=actor,
    )
    try:
        run_id = await _new_run(evidence_store, dag_store, _chain_dag())
        await _record_terminal(
            evidence_store=evidence_store,
            dispatch_store=dispatch_store,
            lease_store=lease_store,
            run_id=run_id,
            task_id="A",
            status=WorkerExecutionStatus.SUCCEEDED,
        )

        outcome = await reconciler.reconcile_run(run_id)

        assert outcome.plan.completed_task_ids == ("A",)
        assert outcome.plan.ready_task_ids == ("B",)
        assert outcome.plan.reconcile_task_ids == ()
        task_b = next(item for item in outcome.plan.tasks if item.task_id == "B")
        assert task_b.frontier_state is DAGTaskFrontierState.WAIT_INTEGRATION_BASE
        assert task_b.execution_base is None
        assert actor.calls == 0
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


def test_integrated_dependency_unlocks_only_downstream_task() -> None:
    asyncio.run(_integrated_dependency_unlocks_only_downstream_task())


async def _integrated_dependency_unlocks_only_downstream_task() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler, task_reconciler = _run_reconciler(
        evidence_store=evidence_store,
        dag_store=dag_store,
        lease_store=lease_store,
        reconciliation_store=reconciliation_store,
        actor=actor,
    )
    try:
        run_id = await _new_run(evidence_store, dag_store, _chain_dag())
        await _record_terminal(
            evidence_store=evidence_store,
            dispatch_store=dispatch_store,
            lease_store=lease_store,
            run_id=run_id,
            task_id="A",
            status=WorkerExecutionStatus.SUCCEEDED,
        )
        await _append_integrated_a(evidence_store=evidence_store, run_id=run_id)

        first = await reconciler.reconcile_run(run_id)
        second = await reconciler.reconcile_run(run_id)

        assert first.plan.completed_task_ids == ("A",)
        assert first.plan.reconcile_task_ids == ("B",)
        task_b = next(item for item in first.plan.tasks if item.task_id == "B")
        assert task_b.frontier_state is DAGTaskFrontierState.RECONCILE_CANDIDATE
        assert task_b.execution_base is not None
        assert task_b.execution_base.commit_sha == _INTEGRATION_A
        assert task_b.execution_base.basis.value == "MERGE_QUEUE_SNAPSHOT"
        assert first.task_outcomes[0].decision.action is TaskReconciliationAction.PREPARED_DISPATCH
        assert (
            second.task_outcomes[0].decision.action
            is TaskReconciliationAction.WAIT_EXISTING_DISPATCH
        )
        assert actor.calls == 1
        assert set(actor.payloads[0]) == {"dispatch_id", "run_id", "task_id"}
        assert actor.payloads[0]["task_id"] == "B"
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


def test_failed_dependency_blocks_descendant_without_dispatch() -> None:
    asyncio.run(_failed_dependency_blocks_descendant_without_dispatch())


async def _failed_dependency_blocks_descendant_without_dispatch() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler, task_reconciler = _run_reconciler(
        evidence_store=evidence_store,
        dag_store=dag_store,
        lease_store=lease_store,
        reconciliation_store=reconciliation_store,
        actor=actor,
    )
    try:
        run_id = await _new_run(evidence_store, dag_store, _chain_dag())
        await _record_terminal(
            evidence_store=evidence_store,
            dispatch_store=dispatch_store,
            lease_store=lease_store,
            run_id=run_id,
            task_id="A",
            status=WorkerExecutionStatus.FAILED,
        )

        outcome = await reconciler.reconcile_run(run_id)

        assert outcome.plan.failed_task_ids == ("A",)
        assert outcome.plan.blocked_task_ids == ("B",)
        assert outcome.plan.reconcile_task_ids == ()
        task_b = next(item for item in outcome.plan.tasks if item.task_id == "B")
        assert task_b.frontier_state is DAGTaskFrontierState.BLOCKED_UPSTREAM_FAILURE
        assert actor.calls == 0
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


def test_active_ready_root_is_never_delegated_to_broker() -> None:
    asyncio.run(_active_ready_root_is_never_delegated_to_broker())


async def _active_ready_root_is_never_delegated_to_broker() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler, task_reconciler = _run_reconciler(
        evidence_store=evidence_store,
        dag_store=dag_store,
        lease_store=lease_store,
        reconciliation_store=reconciliation_store,
        actor=actor,
    )
    try:
        run_id = await _new_run(evidence_store, dag_store, _chain_dag())
        dispatch_id = uuid4()
        await dispatch_store.begin_initial_attempt(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id="A",
        )
        await dispatch_store.mark_enqueued(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id="A",
            broker_message_id="live-root",
            queue_name="devflow_tasks",
        )
        await lease_store.acquire_task_lease(
            run_id=run_id,
            task_id="A",
            owner_id="live-root-worker",
            dispatch_id=dispatch_id,
            lease_seconds=60,
        )

        outcome = await reconciler.reconcile_run(run_id)

        task_a = next(item for item in outcome.plan.tasks if item.task_id == "A")
        task_b = next(item for item in outcome.plan.tasks if item.task_id == "B")
        assert task_a.frontier_state is DAGTaskFrontierState.WAIT_ACTIVE_OWNER
        assert task_b.frontier_state is DAGTaskFrontierState.WAIT_DEPENDENCIES
        assert outcome.plan.reconcile_task_ids == ()
        assert actor.calls == 0
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()


def test_concurrent_run_reconcilers_publish_each_ready_root_once() -> None:
    asyncio.run(_concurrent_run_reconcilers_publish_each_ready_root_once())


async def _concurrent_run_reconcilers_publish_each_ready_root_once() -> None:
    database_url = _database_url()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    actor = _AckActor()
    reconciler, task_reconciler = _run_reconciler(
        evidence_store=evidence_store,
        dag_store=dag_store,
        lease_store=lease_store,
        reconciliation_store=reconciliation_store,
        actor=actor,
    )
    try:
        run_id = await _new_run(evidence_store, dag_store, _parallel_dag())

        first, second = await asyncio.gather(
            reconciler.reconcile_run(run_id),
            reconciler.reconcile_run(run_id),
        )

        assert first.plan.reconcile_task_ids == ("A", "B")
        assert second.plan.reconcile_task_ids == ("A", "B")
        assert actor.calls == 2
        assert {payload["task_id"] for payload in actor.payloads} == {"A", "B"}
        for task_id in ("A", "B"):
            attempts = await dispatch_store.list_for_task(run_id=run_id, task_id=task_id)
            assert len(attempts) == 1
            assert attempts[0].state.value == "ENQUEUED"
    finally:
        await task_reconciler.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()
