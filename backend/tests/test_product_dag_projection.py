from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.api.models import ProductDAGNodeState, ProductDAGStateBasis
from app.api.service import ProductRuntimeService
from app.models.dag import TaskDAG, TaskNode
from app.models.run import RunEvent, TaskRunState
from app.models.task import TaskContract
from app.persistence.dag import PersistedDAGSnapshot, PersistedDAGSource
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.serialization import canonical_payload
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Execute {task_id}.",
        readable_files=["src/**"],
        writable_files=[f"src/{task_id}.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} passes."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("A")),
            TaskNode(task=_task("B"), depends_on=("A",)),
            TaskNode(task=_task("C"), depends_on=("A",)),
            TaskNode(task=_task("D"), depends_on=("B", "C")),
        )
    )


def _evidence(run_id, task_id: str, sequence: int, state: TaskRunState) -> PersistedEvidence:
    event = RunEvent(sequence=sequence, state=state, detail=f"{task_id} -> {state.value}")
    payload, digest = canonical_payload(event)
    return PersistedEvidence(
        id=sequence + (1 if task_id == "A" else 100),
        run_id=run_id,
        task_id=task_id,
        evidence_key=f"{task_id}:state:{sequence}",
        kind=PersistenceEvidenceKind.STATE_TRANSITION,
        stage="runtime",
        sequence=sequence,
        schema_version=1,
        payload=payload,
        payload_sha256=digest,
        created_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


class FakeCatalog:
    async def dispose(self) -> None:
        return None


class FakeEvidenceStore:
    def __init__(self, snapshot: PersistedRunSnapshot) -> None:
        self.snapshot = snapshot

    async def dispose(self) -> None:
        return None

    async def load_run(self, run_id):
        assert run_id == self.snapshot.run_id
        return self.snapshot


class FakeDAGStore:
    def __init__(self, snapshot: PersistedDAGSnapshot) -> None:
        self.snapshot = snapshot

    async def dispose(self) -> None:
        return None

    async def load_dag(self, run_id):
        assert run_id == self.snapshot.run_id
        return self.snapshot


class NotUsed:
    def __getattr__(self, name):
        raise AssertionError(f"unexpected dependency use: {name}")


def _snapshot(
    *,
    run_id,
    project_id,
    dag: TaskDAG,
    evidence: tuple[PersistedEvidence, ...],
) -> PersistedRunSnapshot:
    persisted_tasks = tuple(
        PersistedTask(
            task=dag.node(task_id).task,
            depends_on=dag.node(task_id).depends_on,
            contract_sha256=canonical_payload(dag.node(task_id).task)[1],
            created_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
        for task_id in dag.topological_order()
    )
    return PersistedRunSnapshot(
        run_id=run_id,
        project_id=project_id,
        repository_url="https://example.com/repo.git",
        default_branch="main",
        base_commit="a" * 40,
        status=PersistedRunStatus.RUNNING,
        tasks=persisted_tasks,
        evidence=evidence,
        started_at=datetime(2026, 8, 19, tzinfo=UTC),
    )


def _service(
    *,
    run_snapshot: PersistedRunSnapshot,
    dag_snapshot: PersistedDAGSnapshot,
) -> ProductRuntimeService:
    return ProductRuntimeService(
        catalog=FakeCatalog(),  # type: ignore[arg-type]
        evidence_store=FakeEvidenceStore(run_snapshot),  # type: ignore[arg-type]
        dag_store=FakeDAGStore(dag_snapshot),  # type: ignore[arg-type]
        provisioner=NotUsed(),  # type: ignore[arg-type]
        workspace_resolver=NotUsed(),  # type: ignore[arg-type]
        dispatcher=NotUsed(),  # type: ignore[arg-type]
    )


def test_product_dag_uses_evidence_and_dag_derivation_without_client_inference() -> None:
    run_id = uuid4()
    project_id = uuid4()
    dag = _dag()
    evidence = (
        _evidence(run_id, "A", 0, TaskRunState.PENDING),
        _evidence(run_id, "A", 1, TaskRunState.RUNNING),
        _evidence(run_id, "A", 2, TaskRunState.SUCCEEDED),
        _evidence(run_id, "B", 0, TaskRunState.PENDING),
        _evidence(run_id, "C", 0, TaskRunState.PENDING),
        _evidence(run_id, "C", 1, TaskRunState.RUNNING),
    )
    run_snapshot = _snapshot(
        run_id=run_id,
        project_id=project_id,
        dag=dag,
        evidence=evidence,
    )
    dag_snapshot = PersistedDAGSnapshot(
        run_id=run_id,
        dag=dag,
        dag_sha256=canonical_payload(dag)[1],
        source=PersistedDAGSource.PERSISTED,
    )
    service = _service(run_snapshot=run_snapshot, dag_snapshot=dag_snapshot)

    product = asyncio.run(service.get_run_dag(run_id))
    nodes = {node.task_id: node for node in product.nodes}

    assert product.topological_order == ("A", "B", "C", "D")
    assert [(edge.source_task_id, edge.target_task_id) for edge in product.edges] == [
        ("A", "B"),
        ("A", "C"),
        ("B", "D"),
        ("C", "D"),
    ]
    assert nodes["A"].presentation_state is ProductDAGNodeState.SUCCEEDED
    assert nodes["A"].state_basis is ProductDAGStateBasis.EVIDENCE
    assert nodes["B"].presentation_state is ProductDAGNodeState.READY
    assert nodes["B"].state_basis is ProductDAGStateBasis.DERIVED_DAG
    assert nodes["C"].presentation_state is ProductDAGNodeState.RUNNING
    assert nodes["C"].state_basis is ProductDAGStateBasis.EVIDENCE
    assert nodes["D"].presentation_state is ProductDAGNodeState.PENDING
    assert nodes["D"].layer == 2


def test_product_dag_rejects_advanced_task_downstream_of_failed_dependency() -> None:
    run_id = uuid4()
    project_id = uuid4()
    dag = _dag()
    run_snapshot = _snapshot(
        run_id=run_id,
        project_id=project_id,
        dag=dag,
        evidence=(
            _evidence(run_id, "A", 1, TaskRunState.FAILED),
            _evidence(run_id, "B", 1, TaskRunState.RUNNING),
        ),
    )
    dag_snapshot = PersistedDAGSnapshot(
        run_id=run_id,
        dag=dag,
        dag_sha256=canonical_payload(dag)[1],
        source=PersistedDAGSource.PERSISTED,
    )
    service = _service(run_snapshot=run_snapshot, dag_snapshot=dag_snapshot)

    with pytest.raises(PersistenceCorruptionError, match="downstream of failed tasks"):
        asyncio.run(service.get_run_dag(run_id))
