from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.models.dag import TaskDAG, TaskNode
from app.models.events import RuntimeEventKind
from app.models.task import TaskContract
from app.persistence import (
    PersistedDAGSource,
    PersistenceConflictError,
    PersistenceCorruptionError,
    PersistenceDAGUnavailableError,
    PostgresDAGStore,
    PostgresEvidenceStore,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.models import RunRow


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for DAG persistence tests")
    pytest.skip("PostgreSQL DAG persistence test requires DEVFLOW_DATABASE_URL")


def _task(task_id: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}.",
        readable_files=["src/**"],
        writable_files=[f"src/{task_id}.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} is verified."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    a = _task("A")
    b = _task("B")
    c = _task("C")
    d = _task("D")
    return TaskDAG(
        tasks=(
            TaskNode(task=d, depends_on=("B", "C")),
            TaskNode(task=b, depends_on=("A",)),
            TaskNode(task=a),
            TaskNode(task=c, depends_on=("A",)),
        )
    )


def test_atomic_run_start_persists_dag_hash_edges_and_started_event() -> None:
    asyncio.run(_atomic_run_start())


async def _atomic_run_start() -> None:
    database_url = _database_url()
    dag = _dag()
    evidence = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    project_id = await evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/atomic.git",
        default_branch="main",
    )

    run_id = await dag_store.start_run(
        project_id=project_id,
        dag=dag,
        base_commit="e" * 40,
    )
    persisted = await dag_store.load_dag(run_id)
    events = await evidence.list_runtime_events(run_id)

    assert persisted.source is PersistedDAGSource.PERSISTED
    assert persisted.dag.topological_order() == ["A", "B", "C", "D"]
    assert persisted.dag.node("D").depends_on == ("B", "C")
    assert len(events) == 1
    assert events[0].kind is RuntimeEventKind.RUN_STARTED
    assert events[0].attributes["dag_sha256"] == persisted.dag_sha256

    await evidence.dispose()
    await dag_store.dispose()


def test_atomic_run_start_rolls_back_identity_when_project_is_unknown() -> None:
    asyncio.run(_atomic_run_start_rolls_back())


async def _atomic_run_start_rolls_back() -> None:
    database_url = _database_url()
    dag_store = PostgresDAGStore.from_url(database_url)
    candidate_run_id = uuid4()

    with pytest.raises(ValueError, match="unknown persistence project"):
        await dag_store.start_run(
            project_id=uuid4(),
            dag=_dag(),
            base_commit="f" * 40,
            run_id=candidate_run_id,
        )

    with pytest.raises(ValueError, match="unknown persistence run"):
        await dag_store.load_dag(candidate_run_id)

    await dag_store.dispose()


def test_validated_dag_round_trip_is_idempotent_and_immutable() -> None:
    asyncio.run(_validated_dag_round_trip())


async def _validated_dag_round_trip() -> None:
    database_url = _database_url()
    dag = _dag()
    evidence = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    project_id = await evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/dag.git",
        default_branch="main",
    )
    run_id = await evidence.start_run(
        project_id=project_id,
        tasks=[dag.node(task_id).task for task_id in dag.task_ids],
        base_commit="a" * 40,
    )

    first = await dag_store.persist_dag(run_id=run_id, dag=dag)
    second = await dag_store.persist_dag(run_id=run_id, dag=dag)
    loaded = await dag_store.load_dag(run_id)

    assert first == second == loaded
    assert loaded.source is PersistedDAGSource.PERSISTED
    assert loaded.dag.topological_order() == ["A", "B", "C", "D"]
    assert loaded.dag.node("D").depends_on == ("B", "C")

    changed = TaskDAG(
        tasks=(
            TaskNode(task=_task("A")),
            TaskNode(task=_task("B"), depends_on=("A",)),
            TaskNode(task=_task("C")),
            TaskNode(task=_task("D"), depends_on=("B", "C")),
        )
    )
    with pytest.raises(PersistenceConflictError, match="immutable"):
        await dag_store.persist_dag(run_id=run_id, dag=changed)

    await evidence.dispose()
    await dag_store.dispose()


def test_dag_hash_corruption_fails_closed() -> None:
    asyncio.run(_dag_hash_corruption_fails_closed())


async def _dag_hash_corruption_fails_closed() -> None:
    database_url = _database_url()
    dag = _dag()
    evidence = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    project_id = await evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/corrupt.git",
        default_branch="main",
    )
    run_id = await evidence.start_run(
        project_id=project_id,
        tasks=[dag.node(task_id).task for task_id in dag.task_ids],
        base_commit="b" * 40,
    )
    await dag_store.persist_dag(run_id=run_id, dag=dag)

    engine = create_postgres_engine(database_url)
    session_factory = create_session_factory(engine)
    async with session_factory.begin() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(dag_sha256="0" * 64))

    with pytest.raises(PersistenceCorruptionError, match="DAG payload hash mismatch"):
        await dag_store.load_dag(run_id)

    await engine.dispose()
    await evidence.dispose()
    await dag_store.dispose()


def test_legacy_single_task_is_safe_but_legacy_multi_task_is_not_inferred() -> None:
    asyncio.run(_legacy_topology_boundary())


async def _legacy_topology_boundary() -> None:
    database_url = _database_url()
    evidence = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    project_id = await evidence.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/legacy.git",
        default_branch="main",
    )

    single = _task("single")
    single_run = await evidence.start_run(
        project_id=project_id,
        tasks=[single],
        base_commit="c" * 40,
    )
    implicit = await dag_store.load_dag(single_run)
    assert implicit.source is PersistedDAGSource.IMPLICIT_SINGLE_TASK
    assert implicit.dag.task_ids == ["single"]
    assert implicit.dag.node("single").depends_on == ()

    multi_run = await evidence.start_run(
        project_id=project_id,
        tasks=[_task("left"), _task("right")],
        base_commit="d" * 40,
    )
    with pytest.raises(PersistenceDAGUnavailableError, match="historical multi-task"):
        await dag_store.load_dag(multi_run)

    await evidence.dispose()
    await dag_store.dispose()
