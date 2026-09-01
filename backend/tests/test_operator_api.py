from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.operator import attach_operator_routes
from app.models.lease import TaskLeaseState
from app.models.operator_recovery import (
    OperatorActionExecutionResult,
    OperatorRecoveryPlan,
)
from app.models.run_reconciliation import (
    DAGRunReconciliationPlan,
    DAGTaskFrontierState,
    DAGTaskReconciliationRecord,
    TaskExecutionBase,
    TaskExecutionBaseBasis,
)
from app.runtime.operator_recovery import OperatorRecoveryPlanner


def _plan() -> OperatorRecoveryPlan:
    run_id = uuid4()
    reconciliation = DAGRunReconciliationPlan(
        run_id=run_id,
        run_status="RUNNING",
        dag_sha256="a" * 64,
        topology_source="PERSISTED",
        observed_at=datetime(2026, 8, 22, 6, 0, tzinfo=UTC),
        topological_order=("A",),
        ready_task_ids=("A",),
        reconcile_task_ids=("A",),
        tasks=(
            DAGTaskReconciliationRecord(
                run_id=run_id,
                task_id="A",
                topological_index=0,
                frontier_state=DAGTaskFrontierState.RECONCILE_CANDIDATE,
                lease_state=TaskLeaseState.UNOWNED,
                lease_generation=0,
                execution_base=TaskExecutionBase(
                    run_id=run_id,
                    task_id="A",
                    commit_sha="b" * 40,
                    basis=TaskExecutionBaseBasis.RUN_BASE,
                ),
                reason="ready",
            ),
        ),
    )
    action = OperatorRecoveryPlanner._advance_action(
        reconciliation,
        dispatches={"A": ()},
    )
    return OperatorRecoveryPlan(
        run_id=run_id,
        reconciliation=reconciliation,
        actions=(action,),
    )


class _OperatorService:
    def __init__(self, plan: OperatorRecoveryPlan) -> None:
        self.plan = plan
        self.plan_calls = 0
        self.action_calls: list[tuple] = []
        self.recovery_calls = 0
        self.recovered_run_id = uuid4()

    async def get_operator_recovery_plan(self, run_id):
        self.plan_calls += 1
        assert run_id == self.plan.run_id
        return self.plan

    async def execute_operator_action(self, *, run_id, action_id):
        self.action_calls.append((run_id, action_id))
        return OperatorActionExecutionResult(
            run_id=run_id,
            action=self.plan.actions[0],
            request_evidence_id=1,
            refreshed_plan=self.plan,
        )

    async def recover_interrupted_run(self, run_id):
        assert run_id == self.plan.run_id
        self.recovery_calls += 1
        return {
            "run_id": str(self.recovered_run_id),
            "project_id": str(uuid4()),
            "base_commit": "b" * 40,
            "dag_sha256": "c" * 64,
            "task_ids": ["A"],
            "initial_ready_task_ids": ["A"],
            "launch_state": "QUEUED",
            "dispatches": [
                {
                    "task_id": "A",
                    "state": "QUEUED",
                    "dispatch_id": str(uuid4()),
                    "broker_message_id": "message-1",
                    "queue_name": "default",
                }
            ],
            "reused_existing_run": self.recovery_calls > 1,
        }


def test_operator_recovery_api_returns_server_advertised_actions() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.get(f"/api/v1/runs/{plan.run_id}/operator-recovery")

    assert response.status_code == 200
    body = response.json()
    assert body["diagnostic_only"] is True
    assert body["mutation_requires_fresh_revalidation"] is True
    assert body["actions"][0]["action_id"] == plan.actions[0].action_id
    assert service.plan_calls == 1


def test_operator_recovery_api_rejects_browser_authored_selectors() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.get(
        f"/api/v1/runs/{plan.run_id}/operator-recovery",
        params={"generation": "2", "dispatch_id": str(uuid4())},
    )

    assert response.status_code == 400
    assert service.plan_calls == 0


def test_operator_action_posts_only_opaque_server_action_id() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        f"/api/v1/runs/{plan.run_id}/operator-actions/{plan.actions[0].action_id}"
    )

    assert response.status_code == 200
    assert service.action_calls == [(plan.run_id, plan.actions[0].action_id)]


def test_operator_action_rejects_authority_body_before_service() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        f"/api/v1/runs/{plan.run_id}/operator-actions/{plan.actions[0].action_id}",
        json={
            "task_id": "A",
            "dispatch_id": str(uuid4()),
            "generation": 2,
            "run_token": str(uuid4()),
            "head_sha": "c" * 40,
        },
    )

    assert response.status_code == 400
    assert service.action_calls == []


def test_operator_action_rejects_query_authority_before_service() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        f"/api/v1/runs/{plan.run_id}/operator-actions/{plan.actions[0].action_id}",
        params={"run_token": str(uuid4())},
    )

    assert response.status_code == 400
    assert service.action_calls == []


def test_interrupted_recovery_creates_a_new_server_owned_run() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(f"/api/v1/runs/{plan.run_id}/recover-as-new")

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] != str(plan.run_id)
    assert body["initial_ready_task_ids"] == ["A"]


def test_interrupted_recovery_repeat_returns_the_existing_server_owned_run() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    first = client.post(f"/api/v1/runs/{plan.run_id}/recover-as-new")
    second = client.post(f"/api/v1/runs/{plan.run_id}/recover-as-new")

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["reused_existing_run"] is True


def test_interrupted_recovery_rejects_browser_authored_inputs() -> None:
    plan = _plan()
    service = _OperatorService(plan)
    app = FastAPI()
    attach_operator_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        f"/api/v1/runs/{plan.run_id}/recover-as-new",
        json={"base_commit": "a" * 40},
    )

    assert response.status_code == 400
