from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.dispatch import WorkerExecutionStatus
from app.models.lease import TaskLeaseSnapshot, TaskLeaseState
from app.models.recovery import RecoveryDisposition, RunRecoveryPlan
from app.models.run_reconciliation import (
    DAGRunReconciliationOutcome,
    DAGRunReconciliationPlan,
    DAGTaskFrontierState,
    DAGTaskReconciliationRecord,
    TaskExecutionBase,
)
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus
from app.runtime.execution_base import TaskExecutionBaseUnavailableError
from app.runtime.recovery import RecoveryStateClassifier


class DAGRunSnapshotReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class DAGRunDAGReader(Protocol):
    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot: ...


class DAGRunLeaseReader(Protocol):
    async def list_task_leases(self, run_id: UUID) -> tuple[TaskLeaseSnapshot, ...]: ...


class DAGRunTaskReconciler(Protocol):
    async def reconcile(self, *, run_id: UUID, task_id: str): ...


class DAGRunExecutionBaseResolver(Protocol):
    async def resolve(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> TaskExecutionBase: ...


class DAGRunReconciliationPlanner:
    """Reconstruct one legal scheduling frontier without persisting scheduler state."""

    async def build_plan(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        persisted_dag: PersistedDAGSnapshot,
        recovery: RunRecoveryPlan,
        execution_base_resolver: DAGRunExecutionBaseResolver,
    ) -> DAGRunReconciliationPlan:
        self._validate_identity(
            snapshot=snapshot,
            persisted_dag=persisted_dag,
            recovery=recovery,
        )
        dag = persisted_dag.dag
        order = tuple(dag.topological_order())
        assessments = {item.task_id: item for item in recovery.tasks}

        completed = {
            task_id
            for task_id, item in assessments.items()
            if item.worker_execution_status is WorkerExecutionStatus.SUCCEEDED
        }
        failed = {
            task_id
            for task_id, item in assessments.items()
            if item.worker_execution_status is WorkerExecutionStatus.FAILED
        }

        try:
            blocked = set(dag.blocked_task_ids(failed_task_ids=failed))
            ready = set(
                dag.ready_task_ids(
                    completed_task_ids=completed,
                    failed_task_ids=failed,
                )
            )
        except ValueError as exc:
            raise PersistenceCorruptionError(
                f"persisted DAG terminal evidence is inconsistent: {exc}"
            ) from exc

        if snapshot.status is PersistedRunStatus.RUNNING:
            advanced_blocked = {
                task_id
                for task_id in blocked
                if assessments[task_id].worker_execution_status is not None
                or assessments[task_id].lease_state is not TaskLeaseState.UNOWNED
            }
            if advanced_blocked:
                raise PersistenceCorruptionError(
                    "tasks downstream of accepted failure already contain execution ownership: "
                    + ", ".join(sorted(advanced_blocked))
                )

        records: list[DAGTaskReconciliationRecord] = []
        reconcile_task_ids: list[str] = []
        ready_ordered = tuple(task_id for task_id in order if task_id in ready)

        for index, task_id in enumerate(order):
            assessment = assessments[task_id]
            node = dag.node(task_id)
            state: DAGTaskFrontierState
            reason: str
            execution_base: TaskExecutionBase | None = None

            if snapshot.status is not PersistedRunStatus.RUNNING:
                state = DAGTaskFrontierState.RUN_TERMINAL
                reason = "Persisted Run is terminal; DAG reconciliation cannot reopen it."
            elif task_id in completed:
                state = DAGTaskFrontierState.SUCCEEDED
                reason = (
                    "Accepted successful WORKER_EXECUTION evidence is terminal task truth; "
                    "reconciliation must not rerun it."
                )
            elif task_id in failed:
                state = DAGTaskFrontierState.FAILED
                reason = (
                    "Accepted failed WORKER_EXECUTION evidence is terminal task truth; "
                    "recovery does not relabel semantic/runtime failure as crash retry."
                )
            elif task_id in blocked:
                state = DAGTaskFrontierState.BLOCKED_UPSTREAM_FAILURE
                reason = "Validated DAG dependencies place this task downstream of failed work."
            elif task_id not in ready:
                state = DAGTaskFrontierState.WAIT_DEPENDENCIES
                reason = "One or more validated DAG dependencies lack successful terminal evidence."
            elif assessment.lease_state is TaskLeaseState.ACTIVE:
                if assessment.disposition is not RecoveryDisposition.WAIT_ACTIVE_OWNER:
                    raise PersistenceCorruptionError(
                        f"ACTIVE task {task_id!r} has inconsistent recovery disposition"
                    )
                state = DAGTaskFrontierState.WAIT_ACTIVE_OWNER
                reason = (
                    "Task is DAG-ready but its current fenced generation is still ACTIVE under "
                    "database time."
                )
            elif assessment.lease_state is TaskLeaseState.RELEASED:
                if assessment.disposition is not RecoveryDisposition.BLOCKED_RELEASED_EVIDENCE_GAP:
                    raise PersistenceCorruptionError(
                        f"RELEASED task {task_id!r} has inconsistent recovery disposition"
                    )
                state = DAGTaskFrontierState.BLOCKED_RECOVERY_GAP
                reason = (
                    "Released ownership lacks terminal worker evidence; DAG recovery cannot "
                    "silently reacquire it."
                )
            elif assessment.lease_state in {TaskLeaseState.UNOWNED, TaskLeaseState.EXPIRED}:
                try:
                    execution_base = await execution_base_resolver.resolve(
                        snapshot=snapshot,
                        task_id=task_id,
                    )
                except TaskExecutionBaseUnavailableError as exc:
                    state = DAGTaskFrontierState.WAIT_INTEGRATION_BASE
                    reason = str(exc)
                else:
                    state = DAGTaskFrontierState.RECONCILE_CANDIDATE
                    reason = (
                        "DAG dependencies and execution base are accepted; Step 5.3 must freshly "
                        "revalidate ownership and dispatch history before any broker publication."
                    )
                    reconcile_task_ids.append(task_id)
            else:
                raise PersistenceCorruptionError(
                    "unsupported lease state for DAG reconciliation: "
                    f"{assessment.lease_state.value}"
                )

            records.append(
                DAGTaskReconciliationRecord(
                    run_id=snapshot.run_id,
                    task_id=task_id,
                    depends_on=node.depends_on,
                    topological_index=index,
                    frontier_state=state,
                    lease_state=assessment.lease_state,
                    lease_generation=assessment.lease_generation,
                    lease_dispatch_id=assessment.lease_dispatch_id,
                    worker_execution_status=assessment.worker_execution_status,
                    worker_execution_evidence_id=assessment.worker_execution_evidence_id,
                    execution_base=execution_base,
                    reason=reason,
                )
            )

        return DAGRunReconciliationPlan(
            run_id=snapshot.run_id,
            run_status=snapshot.status.value,
            dag_sha256=persisted_dag.dag_sha256,
            topology_source=persisted_dag.source.value,
            observed_at=recovery.observed_at,
            topological_order=order,
            completed_task_ids=tuple(task_id for task_id in order if task_id in completed),
            failed_task_ids=tuple(task_id for task_id in order if task_id in failed),
            blocked_task_ids=tuple(task_id for task_id in order if task_id in blocked),
            ready_task_ids=ready_ordered,
            reconcile_task_ids=tuple(reconcile_task_ids),
            tasks=tuple(records),
        )

    @staticmethod
    def _validate_identity(
        *,
        snapshot: PersistedRunSnapshot,
        persisted_dag: PersistedDAGSnapshot,
        recovery: RunRecoveryPlan,
    ) -> None:
        if persisted_dag.run_id != snapshot.run_id or recovery.run_id != snapshot.run_id:
            raise PersistenceCorruptionError("DAG reconciliation Run identities disagree")
        if recovery.run_status != snapshot.status.value:
            raise PersistenceCorruptionError(
                "recovery plan Run status disagrees with persisted Run"
            )
        snapshot_task_ids = {item.task.task_id for item in snapshot.tasks}
        dag_task_ids = set(persisted_dag.dag.task_ids)
        recovery_task_ids = {item.task_id for item in recovery.tasks}
        if snapshot_task_ids != dag_task_ids or snapshot_task_ids != recovery_task_ids:
            raise PersistenceCorruptionError(
                "persisted Run, DAG, and recovery task identities must match exactly"
            )


class DAGRunReconciler:
    """Delegate only the currently legal DAG frontier to the accepted Step 5.3 authority."""

    def __init__(
        self,
        *,
        run_reader: DAGRunSnapshotReader,
        dag_reader: DAGRunDAGReader,
        lease_reader: DAGRunLeaseReader,
        task_reconciler: DAGRunTaskReconciler,
        execution_base_resolver: DAGRunExecutionBaseResolver,
        classifier: RecoveryStateClassifier | None = None,
        planner: DAGRunReconciliationPlanner | None = None,
    ) -> None:
        self._run_reader = run_reader
        self._dag_reader = dag_reader
        self._lease_reader = lease_reader
        self._task_reconciler = task_reconciler
        self._execution_base_resolver = execution_base_resolver
        self._classifier = classifier or RecoveryStateClassifier()
        self._planner = planner or DAGRunReconciliationPlanner()

    async def reconcile_run(self, run_id: UUID) -> DAGRunReconciliationOutcome:
        snapshot = await self._run_reader.load_run(run_id)
        persisted_dag = await self._dag_reader.load_dag(run_id)
        leases = await self._lease_reader.list_task_leases(run_id)
        recovery = self._classifier.classify(snapshot=snapshot, leases=leases)
        plan = await self._planner.build_plan(
            snapshot=snapshot,
            persisted_dag=persisted_dag,
            recovery=recovery,
            execution_base_resolver=self._execution_base_resolver,
        )

        outcomes = []
        for task_id in plan.reconcile_task_ids:
            outcomes.append(
                await self._task_reconciler.reconcile(run_id=run_id, task_id=task_id)
            )
        return DAGRunReconciliationOutcome(plan=plan, task_outcomes=tuple(outcomes))
