from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.models.dispatch_attempt import PersistedDispatchAttempt
from app.models.operator_recovery import (
    OperatorAction,
    OperatorActionExecutionResult,
    OperatorActionKind,
    OperatorActionRequestEvidence,
    OperatorRecoveryPlan,
)
from app.models.run_reconciliation import (
    DAGRunReconciliationPlan,
    DAGTaskFrontierState,
)
from app.persistence.errors import PersistenceConflictError
from app.persistence.serialization import payload_sha256
from app.persistence.types import PersistedRunSnapshot, PersistenceEvidenceKind
from app.runtime.recovery import RecoveryStateClassifier
from app.runtime.run_reconciler import DAGRunReconciliationPlanner


class OperatorRunReader(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class OperatorDAGReader(Protocol):
    async def load_dag(self, run_id: UUID): ...


class OperatorLeaseReader(Protocol):
    async def list_task_leases(self, run_id: UUID): ...


class OperatorDispatchReader(Protocol):
    async def list_for_task(
        self,
        *,
        run_id: UUID,
        task_id: str,
    ) -> tuple[PersistedDispatchAttempt, ...]: ...


class OperatorExecutionBaseResolver(Protocol):
    async def resolve(self, *, snapshot: PersistedRunSnapshot, task_id: str): ...


class OperatorAuditStore(Protocol):
    async def append_evidence(self, **kwargs) -> int: ...


class OperatorRunController(Protocol):
    async def advance(self, run_id: UUID): ...


class OperatorRunReconciler(Protocol):
    async def reconcile_run(self, run_id: UUID): ...


class OperatorActionStaleError(PersistenceConflictError):
    """Raised when a browser action id no longer matches freshly reconstructed authority."""


class OperatorRecoveryPlanner:
    """Build a bounded operator view and bind actions to durable dispatch history."""

    _ADVANCE_STATES = {
        DAGTaskFrontierState.SUCCEEDED,
        DAGTaskFrontierState.FAILED,
        DAGTaskFrontierState.BLOCKED_UPSTREAM_FAILURE,
        DAGTaskFrontierState.WAIT_INTEGRATION_BASE,
        DAGTaskFrontierState.RECONCILE_CANDIDATE,
    }

    def __init__(
        self,
        *,
        run_reader: OperatorRunReader,
        dag_reader: OperatorDAGReader,
        lease_reader: OperatorLeaseReader,
        dispatch_reader: OperatorDispatchReader,
        execution_base_resolver: OperatorExecutionBaseResolver,
        classifier: RecoveryStateClassifier | None = None,
        reconciliation_planner: DAGRunReconciliationPlanner | None = None,
    ) -> None:
        self._run_reader = run_reader
        self._dag_reader = dag_reader
        self._lease_reader = lease_reader
        self._dispatch_reader = dispatch_reader
        self._execution_base_resolver = execution_base_resolver
        self._classifier = classifier or RecoveryStateClassifier()
        self._reconciliation_planner = reconciliation_planner or DAGRunReconciliationPlanner()

    async def build_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        snapshot = await self._run_reader.load_run(run_id)
        persisted_dag = await self._dag_reader.load_dag(run_id)
        leases = await self._lease_reader.list_task_leases(run_id)
        recovery = self._classifier.classify(snapshot=snapshot, leases=leases)
        reconciliation = await self._reconciliation_planner.build_plan(
            snapshot=snapshot,
            persisted_dag=persisted_dag,
            recovery=recovery,
            execution_base_resolver=self._execution_base_resolver,
        )
        dispatches = {
            task_id: await self._dispatch_reader.list_for_task(
                run_id=run_id,
                task_id=task_id,
            )
            for task_id in reconciliation.topological_order
        }
        actions: tuple[OperatorAction, ...] = ()
        if self._should_offer_advance(reconciliation):
            actions = (self._advance_action(reconciliation, dispatches=dispatches),)
        return OperatorRecoveryPlan(
            run_id=run_id,
            reconciliation=reconciliation,
            actions=actions,
        )

    @classmethod
    def _should_offer_advance(cls, plan: DAGRunReconciliationPlan) -> bool:
        if plan.run_status != "RUNNING":
            return False
        return any(item.frontier_state in cls._ADVANCE_STATES for item in plan.tasks)

    @classmethod
    def _advance_action(
        cls,
        plan: DAGRunReconciliationPlan,
        *,
        dispatches: dict[str, tuple[PersistedDispatchAttempt, ...]],
    ) -> OperatorAction:
        return OperatorAction(
            action_id=cls._action_id(
                plan=plan,
                kind=OperatorActionKind.ADVANCE_RUN,
                dispatches=dispatches,
            ),
            kind=OperatorActionKind.ADVANCE_RUN,
            label="Advance from durable facts",
            description=(
                "Re-enter the accepted durable controller/reconciler. This does not force a "
                "retry and cannot bypass fresh lease, dispatch, DAG, Git, or fencing checks."
            ),
        )

    @staticmethod
    def _action_id(
        *,
        plan: DAGRunReconciliationPlan,
        kind: OperatorActionKind,
        dispatches: dict[str, tuple[PersistedDispatchAttempt, ...]],
    ) -> str:
        tasks = []
        for item in plan.tasks:
            execution_base = None
            if item.execution_base is not None:
                execution_base = {
                    "commit_sha": item.execution_base.commit_sha,
                    "basis": item.execution_base.basis.value,
                    "source_evidence_id": item.execution_base.source_evidence_id,
                    "source_evidence_sha256": item.execution_base.source_evidence_sha256,
                    "integration_ref": item.execution_base.integration_ref,
                }
            task_dispatches = tuple(dispatches.get(item.task_id, ()))
            tasks.append(
                {
                    "task_id": item.task_id,
                    "frontier_state": item.frontier_state.value,
                    "lease_state": item.lease_state.value,
                    "lease_generation": item.lease_generation,
                    "lease_dispatch_id": (
                        str(item.lease_dispatch_id) if item.lease_dispatch_id is not None else None
                    ),
                    "worker_execution_status": (
                        item.worker_execution_status.value
                        if item.worker_execution_status is not None
                        else None
                    ),
                    "worker_execution_evidence_id": item.worker_execution_evidence_id,
                    "execution_base": execution_base,
                    "dispatch_attempts": [
                        {
                            "dispatch_id": str(attempt.dispatch_id),
                            "attempt_number": attempt.attempt_number,
                            "state": attempt.state.value,
                            "broker_message_id": attempt.broker_message_id,
                            "queue_name": attempt.queue_name,
                            "error_code": attempt.error_code,
                        }
                        for attempt in task_dispatches
                    ],
                }
            )
        return payload_sha256(
            {
                "schema_version": 1,
                "run_id": str(plan.run_id),
                "kind": kind.value,
                "run_status": plan.run_status,
                "dag_sha256": plan.dag_sha256,
                "topology_source": plan.topology_source,
                "topological_order": list(plan.topological_order),
                "tasks": tasks,
            }
        )


class OperatorRecoveryCoordinator:
    """Accept one opaque operator action only after rebuilding its durable justification."""

    def __init__(
        self,
        *,
        planner: OperatorRecoveryPlanner,
        audit_store: OperatorAuditStore,
        run_controller: OperatorRunController,
        run_reconciler: OperatorRunReconciler,
    ) -> None:
        self._planner = planner
        self._audit_store = audit_store
        self._run_controller = run_controller
        self._run_reconciler = run_reconciler

    async def get_plan(self, run_id: UUID) -> OperatorRecoveryPlan:
        return await self._planner.build_plan(run_id)

    async def execute(self, *, run_id: UUID, action_id: str) -> OperatorActionExecutionResult:
        fresh = await self._planner.build_plan(run_id)
        action = next((item for item in fresh.actions if item.action_id == action_id), None)
        if action is None:
            raise OperatorActionStaleError(
                "operator action is stale or no longer authorized by the current durable state"
            )

        request = OperatorActionRequestEvidence(
            run_id=run_id,
            action_id=action.action_id,
            kind=action.kind,
        )
        evidence_id = await self._audit_store.append_evidence(
            run_id=run_id,
            evidence_key=f"operator:request:{action.action_id}",
            kind=PersistenceEvidenceKind.OPERATOR_ACTION,
            payload_model=request,
            task_id=None,
            stage="operator",
        )

        if action.kind is not OperatorActionKind.ADVANCE_RUN:
            raise PersistenceConflictError("unsupported operator action kind")
        if len(fresh.reconciliation.tasks) == 1:
            await self._run_reconciler.reconcile_run(run_id)
        else:
            await self._run_controller.advance(run_id)

        refreshed = await self._planner.build_plan(run_id)
        return OperatorActionExecutionResult(
            run_id=run_id,
            action=action,
            request_evidence_id=evidence_id,
            refreshed_plan=refreshed,
        )
