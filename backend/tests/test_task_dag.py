import pytest
from pydantic import ValidationError

from app import models


def _task(task_id: str) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}",
        readable_files=["**"],
        writable_files=[f"src/{task_id}.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} is complete"],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=2,
    )


def _node(task_id: str, *dependencies: str) -> models.TaskNode:
    return models.TaskNode(task=_task(task_id), depends_on=list(dependencies))


def _sample_dag() -> models.TaskDAG:
    return models.TaskDAG(
        tasks=[
            _node("TASK-005", "TASK-003", "TASK-004"),
            _node("TASK-003", "TASK-002"),
            _node("TASK-001"),
            _node("TASK-004", "TASK-002"),
            _node("TASK-002", "TASK-001"),
        ]
    )


def test_valid_dag_has_deterministic_topological_order() -> None:
    dag = _sample_dag()

    assert dag.topological_order() == [
        "TASK-001",
        "TASK-002",
        "TASK-003",
        "TASK-004",
        "TASK-005",
    ]


def test_dag_structure_is_immutable_after_validation() -> None:
    dag = _sample_dag()

    assert isinstance(dag.tasks, tuple)
    assert isinstance(dag.node("TASK-003").depends_on, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        dag.tasks = ()
    with pytest.raises(ValidationError, match="frozen"):
        dag.node("TASK-003").depends_on = ()


def test_dag_json_round_trip_preserves_validated_graph() -> None:
    dag = _sample_dag()

    restored = models.TaskDAG.model_validate_json(dag.model_dump_json())

    assert restored == dag
    assert restored.topological_order() == dag.topological_order()


def test_task_node_rejects_duplicate_dependencies() -> None:
    with pytest.raises(ValidationError, match="duplicate task ids"):
        _node("TASK-002", "TASK-001", "TASK-001")


def test_task_node_rejects_self_dependency() -> None:
    with pytest.raises(ValidationError, match="must not depend on itself"):
        _node("TASK-001", "TASK-001")


def test_task_node_rejects_invalid_dependency_identifier() -> None:
    with pytest.raises(ValidationError, match="TaskContract task_id format"):
        _node("TASK-002", "../TASK-001")


def test_dag_rejects_duplicate_task_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate task ids"):
        models.TaskDAG(tasks=[_node("TASK-001"), _node("TASK-001")])


def test_dag_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies: TASK-404"):
        models.TaskDAG(tasks=[_node("TASK-001", "TASK-404")])


def test_dag_rejects_dependency_cycle() -> None:
    with pytest.raises(ValidationError, match="dependency cycle"):
        models.TaskDAG(
            tasks=[
                _node("TASK-001", "TASK-003"),
                _node("TASK-002", "TASK-001"),
                _node("TASK-003", "TASK-002"),
            ]
        )


def test_node_lookup_returns_validated_node_and_rejects_unknown_id() -> None:
    dag = _sample_dag()

    assert dag.node("TASK-003").depends_on == ("TASK-002",)
    with pytest.raises(KeyError, match="unknown task id: TASK-404"):
        dag.node("TASK-404")


def test_ready_tasks_progress_with_completed_dependencies() -> None:
    dag = _sample_dag()

    assert dag.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()) == ["TASK-001"]
    assert dag.ready_task_ids(
        completed_task_ids={"TASK-001"},
        failed_task_ids=set(),
    ) == ["TASK-002"]
    assert dag.ready_task_ids(
        completed_task_ids={"TASK-001", "TASK-002"},
        failed_task_ids=set(),
    ) == ["TASK-003", "TASK-004"]
    assert dag.ready_task_ids(
        completed_task_ids={"TASK-001", "TASK-002", "TASK-003", "TASK-004"},
        failed_task_ids=set(),
    ) == ["TASK-005"]


def test_failed_task_blocks_all_transitive_descendants() -> None:
    dag = _sample_dag()

    assert dag.blocked_task_ids(failed_task_ids={"TASK-002"}) == [
        "TASK-003",
        "TASK-004",
        "TASK-005",
    ]
    assert dag.ready_task_ids(
        completed_task_ids={"TASK-001"},
        failed_task_ids={"TASK-002"},
    ) == []


def test_one_failed_branch_does_not_block_independent_ready_sibling() -> None:
    dag = _sample_dag()

    assert dag.blocked_task_ids(failed_task_ids={"TASK-003"}) == ["TASK-005"]
    assert dag.ready_task_ids(
        completed_task_ids={"TASK-001", "TASK-002"},
        failed_task_ids={"TASK-003"},
    ) == ["TASK-004"]


def test_runtime_state_rejects_unknown_task_ids() -> None:
    dag = _sample_dag()

    with pytest.raises(ValueError, match="unknown completed task ids: TASK-404"):
        dag.ready_task_ids(completed_task_ids={"TASK-404"}, failed_task_ids=set())
    with pytest.raises(ValueError, match="unknown failed task ids: TASK-404"):
        dag.blocked_task_ids(failed_task_ids={"TASK-404"})


def test_runtime_state_rejects_completed_and_failed_overlap() -> None:
    dag = _sample_dag()

    with pytest.raises(ValueError, match="both completed and failed: TASK-001"):
        dag.ready_task_ids(
            completed_task_ids={"TASK-001"},
            failed_task_ids={"TASK-001"},
        )


def test_runtime_state_rejects_completed_task_downstream_of_failure() -> None:
    dag = _sample_dag()

    with pytest.raises(ValueError, match="completed tasks cannot be downstream"):
        dag.ready_task_ids(
            completed_task_ids={"TASK-001", "TASK-003"},
            failed_task_ids={"TASK-002"},
        )
