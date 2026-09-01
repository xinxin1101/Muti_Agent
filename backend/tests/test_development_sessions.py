from __future__ import annotations

from app.api.autonomous import AutonomousProductRuntimeService
from app.models.dag import TaskDAG, TaskNode
from app.models.task import TaskContract


def _task(task_id: str, path: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}.",
        readable_files=["src/**"],
        writable_files=[path],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} is verified."],
        verification_commands=["pytest -q"],
    )


def test_recovery_dag_skips_completed_work_package_and_releases_consumer() -> None:
    dag = TaskDAG(
        tasks=(
            TaskNode(task=_task("core", "src/core.py")),
            TaskNode(
                task=_task("web", "src/web.py"),
                depends_on=("core",),
            ),
        )
    )

    remaining = AutonomousProductRuntimeService._remaining_session_dag(dag, {"core"})

    assert remaining is not None
    assert remaining.task_ids == ["web"]
    assert remaining.node("web").depends_on == ()
    assert remaining.ready_task_ids(completed_task_ids=set(), failed_task_ids=set()) == ["web"]


def test_recovery_dag_is_absent_when_every_work_package_is_complete() -> None:
    dag = TaskDAG(tasks=(TaskNode(task=_task("core", "src/core.py")),))

    assert AutonomousProductRuntimeService._remaining_session_dag(dag, {"core"}) is None
