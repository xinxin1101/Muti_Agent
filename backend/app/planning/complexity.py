from __future__ import annotations

from dataclasses import dataclass

from app.models.dag import TaskDAG
from app.models.task import TaskContract


@dataclass(frozen=True)
class TaskComplexity:
    """A deterministic guard against assigning a whole feature to one agent turn loop."""

    task_id: str
    score: int
    reasons: tuple[str, ...]

    @property
    def oversized(self) -> bool:
        return self.score >= 6


@dataclass(frozen=True)
class DAGComplexityReport:
    tasks: tuple[TaskComplexity, ...]

    @property
    def oversized_tasks(self) -> tuple[TaskComplexity, ...]:
        return tuple(item for item in self.tasks if item.oversized)

    @property
    def acceptable(self) -> bool:
        return not self.oversized_tasks

    def summary(self) -> str:
        if self.acceptable:
            return "All tasks fit the single-agent implementation budget."
        details = "; ".join(
            f"{item.task_id} (score={item.score}: {', '.join(item.reasons)})"
            for item in self.oversized_tasks
        )
        return (
            "The following tasks are too broad for one bounded Developer Agent and must be "
            f"split into independently verifiable tasks: {details}."
        )


def assess_dag_complexity(dag: TaskDAG) -> DAGComplexityReport:
    return DAGComplexityReport(tasks=tuple(assess_task_complexity(node.task) for node in dag.tasks))


def assess_task_complexity(task: TaskContract) -> TaskComplexity:
    """Return the deterministic complexity signal used by both planner and worker."""
    score = 0
    reasons: list[str] = []
    if len(task.objective) > 900:
        score += 2
        reasons.append("objective is longer than 900 characters")
    elif len(task.objective) > 450:
        score += 1
        reasons.append("objective is longer than 450 characters")

    writable_count = len(task.writable_files)
    if writable_count > 4:
        score += 3
        reasons.append(f"owns {writable_count} writable scopes")
    elif writable_count > 2:
        score += 1
        reasons.append(f"owns {writable_count} writable scopes")

    broad_scopes = sum(1 for path in task.writable_files if "*" in path)
    if broad_scopes:
        score += min(2, broad_scopes)
        reasons.append("writable scope uses broad glob patterns")

    if len(task.acceptance_criteria) > 4:
        score += 2
        reasons.append(f"contains {len(task.acceptance_criteria)} acceptance criteria")
    elif len(task.acceptance_criteria) > 2:
        score += 1
        reasons.append(f"contains {len(task.acceptance_criteria)} acceptance criteria")

    if len(task.verification_commands) > 3:
        score += 1
        reasons.append(f"requires {len(task.verification_commands)} verification commands")
    return TaskComplexity(task_id=task.task_id, score=score, reasons=tuple(reasons))
