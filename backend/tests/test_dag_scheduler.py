import pytest
from pydantic import ValidationError

from app import models, runtime


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
    return models.TaskNode(task=_task(task_id), depends_on=dependencies)


def _sample_dag() -> models.TaskDAG:
    return models.TaskDAG(
        tasks=(
            _node("TASK-005", "TASK-003", "TASK-004"),
            _node("TASK-003", "TASK-002"),
            _node("TASK-001"),
            _node("TASK-004", "TASK-002"),
            _node("TASK-002", "TASK-001"),
        )
    )


def test_scheduler_initializes_only_dependency_free_tasks_as_ready() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    assert scheduler.state("TASK-001") is models.TaskScheduleState.READY
    assert scheduler.ready_task_ids() == ("TASK-001",)
    assert scheduler.state("TASK-002") is models.TaskScheduleState.PENDING
    assert scheduler.state("TASK-005") is models.TaskScheduleState.PENDING
    assert not scheduler.is_terminal


def test_pending_task_cannot_start_before_dependencies_succeed() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    with pytest.raises(ValueError, match="must be READY"):
        scheduler.start("TASK-002")

    assert scheduler.state("TASK-002") is models.TaskScheduleState.PENDING


def test_success_unlocks_newly_ready_dependents() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")
    assert scheduler.ready_task_ids() == ("TASK-002",)

    scheduler.start("TASK-002")
    scheduler.succeed("TASK-002")
    assert scheduler.ready_task_ids() == ("TASK-003", "TASK-004")


def test_failed_task_blocks_all_transitive_descendants() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")
    scheduler.start("TASK-002")
    scheduler.fail("TASK-002")

    assert scheduler.state("TASK-001") is models.TaskScheduleState.SUCCEEDED
    assert scheduler.state("TASK-002") is models.TaskScheduleState.FAILED
    assert scheduler.state("TASK-003") is models.TaskScheduleState.BLOCKED
    assert scheduler.state("TASK-004") is models.TaskScheduleState.BLOCKED
    assert scheduler.state("TASK-005") is models.TaskScheduleState.BLOCKED
    assert scheduler.ready_task_ids() == ()
    assert scheduler.is_terminal


def test_failed_branch_does_not_block_independent_ready_sibling() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")
    scheduler.start("TASK-002")
    scheduler.succeed("TASK-002")
    scheduler.start("TASK-003")
    scheduler.fail("TASK-003")

    assert scheduler.state("TASK-003") is models.TaskScheduleState.FAILED
    assert scheduler.state("TASK-005") is models.TaskScheduleState.BLOCKED
    assert scheduler.state("TASK-004") is models.TaskScheduleState.READY
    assert scheduler.ready_task_ids() == ("TASK-004",)


def test_blocked_task_cannot_be_started() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")
    scheduler.start("TASK-002")
    scheduler.fail("TASK-002")

    with pytest.raises(ValueError, match="current state is BLOCKED"):
        scheduler.start("TASK-003")


def test_terminal_task_cannot_restart_or_change_terminal_outcome() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")

    with pytest.raises(ValueError, match="current state is SUCCEEDED"):
        scheduler.start("TASK-001")
    with pytest.raises(ValueError, match="must be RUNNING"):
        scheduler.fail("TASK-001")


def test_only_running_tasks_can_succeed_or_fail() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    with pytest.raises(ValueError, match="must be RUNNING"):
        scheduler.succeed("TASK-001")
    with pytest.raises(ValueError, match="must be RUNNING"):
        scheduler.fail("TASK-001")


def test_unknown_task_ids_fail_closed() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    with pytest.raises(KeyError, match="unknown task id: TASK-404"):
        scheduler.state("TASK-404")
    with pytest.raises(KeyError, match="unknown task id: TASK-404"):
        scheduler.start("TASK-404")


def test_multiple_independent_roots_can_be_marked_running_without_executing_workers() -> None:
    dag = models.TaskDAG(tasks=(_node("TASK-A"), _node("TASK-B")))
    scheduler = runtime.DAGScheduler(dag)

    assert scheduler.ready_task_ids() == ("TASK-A", "TASK-B")
    scheduler.start("TASK-A")
    scheduler.start("TASK-B")

    assert scheduler.task_ids_in_state(models.TaskScheduleState.RUNNING) == (
        "TASK-A",
        "TASK-B",
    )
    assert scheduler.ready_task_ids() == ()


def test_multi_dependency_task_stays_blocked_after_one_parent_fails() -> None:
    dag = models.TaskDAG(
        tasks=(
            _node("TASK-A"),
            _node("TASK-B"),
            _node("TASK-C", "TASK-A", "TASK-B"),
        )
    )
    scheduler = runtime.DAGScheduler(dag)

    scheduler.start("TASK-A")
    scheduler.fail("TASK-A")
    assert scheduler.state("TASK-C") is models.TaskScheduleState.BLOCKED
    assert scheduler.state("TASK-B") is models.TaskScheduleState.READY

    scheduler.start("TASK-B")
    scheduler.succeed("TASK-B")
    assert scheduler.state("TASK-C") is models.TaskScheduleState.BLOCKED
    assert scheduler.ready_task_ids() == ()


def test_all_success_path_reaches_terminal_state() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    for task_id in ("TASK-001", "TASK-002", "TASK-003", "TASK-004", "TASK-005"):
        scheduler.start(task_id)
        scheduler.succeed(task_id)

    assert scheduler.is_terminal
    assert scheduler.task_ids_in_state(models.TaskScheduleState.SUCCEEDED) == (
        "TASK-001",
        "TASK-002",
        "TASK-003",
        "TASK-004",
        "TASK-005",
    )


def test_scheduler_events_are_deterministic_and_auditable() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())

    scheduler.start("TASK-001")
    scheduler.succeed("TASK-001")

    events = scheduler.events
    assert [event.sequence for event in events] == [0, 1, 2, 3]
    assert [event.to_state for event in events] == [
        models.TaskScheduleState.READY,
        models.TaskScheduleState.RUNNING,
        models.TaskScheduleState.SUCCEEDED,
        models.TaskScheduleState.READY,
    ]
    assert events[-1].task_id == "TASK-002"


def test_snapshot_is_deterministic_and_does_not_expose_scheduler_mutable_state() -> None:
    scheduler = runtime.DAGScheduler(_sample_dag())
    snapshot = scheduler.snapshot()

    assert tuple(record.task_id for record in snapshot.tasks) == (
        "TASK-001",
        "TASK-002",
        "TASK-003",
        "TASK-004",
        "TASK-005",
    )
    assert snapshot.tasks[0].state is models.TaskScheduleState.READY

    with pytest.raises(ValidationError, match="frozen"):
        snapshot.tasks[0].state = models.TaskScheduleState.FAILED

    assert scheduler.state("TASK-001") is models.TaskScheduleState.READY


def test_scheduler_event_rejects_noop_transition() -> None:
    with pytest.raises(ValidationError, match="real state transition"):
        models.SchedulerEvent(
            sequence=0,
            task_id="TASK-001",
            from_state=models.TaskScheduleState.READY,
            to_state=models.TaskScheduleState.READY,
            detail="invalid no-op",
        )
