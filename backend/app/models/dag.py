from __future__ import annotations

import heapq
import re
from collections import deque

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.task import TaskContract
from app.models.work_package import (
    PlanningComplexity,
    PlanningComplexityAssessment,
    TaskBudgetAllocation,
)
from app.models.workflow import WorkflowExecutionMode

_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class TaskNode(BaseModel):
    """One validated task plus its immutable dependency edges in a V0.2 task graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: TaskContract
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    execution_mode: WorkflowExecutionMode = WorkflowExecutionMode.AGENT
    # These declarations are immutable planning facts.  They deliberately live on the
    # persisted DAG node rather than only on the Planner instance, so a restarted worker
    # can enforce the same producer/consumer boundary without asking a model again.
    produces: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    consumes: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    complexity: PlanningComplexity | None = None
    budget_allocation: TaskBudgetAllocation | None = None
    complexity_assessment: PlanningComplexityAssessment | None = None

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            dependency = value.strip()
            if not dependency:
                raise ValueError("dependency task ids must not be empty")
            if len(dependency) > 128 or _TASK_ID_PATTERN.fullmatch(dependency) is None:
                raise ValueError("dependency task ids must use the TaskContract task_id format")
            normalized.append(dependency)

        if len(normalized) != len(set(normalized)):
            raise ValueError("depends_on must not contain duplicate task ids")
        return tuple(normalized)

    @model_validator(mode="after")
    def reject_self_dependency(self) -> TaskNode:
        if self.task.task_id in self.depends_on:
            raise ValueError(f"task {self.task.task_id!r} must not depend on itself")
        return self

    @model_validator(mode="after")
    def validate_work_package_metadata(self) -> TaskNode:
        if len(self.produces) != len(set(self.produces)):
            raise ValueError("produced interfaces must not contain duplicates")
        if len(self.consumes) != len(set(self.consumes)):
            raise ValueError("consumed interfaces must not contain duplicates")
        if (
            self.budget_allocation is not None
            and self.budget_allocation.package_id != self.task.task_id
        ):
            raise ValueError("task budget allocation must belong to this task")
        if (
            self.complexity_assessment is not None
            and self.complexity_assessment.package_id != self.task.task_id
        ):
            raise ValueError("task complexity assessment must belong to this task")
        if (
            self.complexity is not None
            and self.complexity_assessment is not None
            and self.complexity is not self.complexity_assessment.complexity
        ):
            raise ValueError("task complexity and assessment complexity must agree")
        return self


class TaskDAG(BaseModel):
    """Validated immutable acyclic task graph with deterministic graph queries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: tuple[TaskNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> TaskDAG:
        task_ids = [node.task.task_id for node in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task DAG must not contain duplicate task ids")

        known = set(task_ids)
        missing = sorted(
            {
                dependency
                for node in self.tasks
                for dependency in node.depends_on
                if dependency not in known
            }
        )
        if missing:
            raise ValueError(f"task DAG references unknown dependencies: {', '.join(missing)}")

        self.topological_order()
        return self

    @property
    def task_ids(self) -> list[str]:
        return [node.task.task_id for node in self.tasks]

    def node(self, task_id: str) -> TaskNode:
        try:
            return self._nodes_by_id()[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task id: {task_id}") from exc

    def topological_order(self) -> list[str]:
        """Return a deterministic topological order or reject cyclic graphs."""

        nodes = self._nodes_by_id()
        indegree = {task_id: len(node.depends_on) for task_id, node in nodes.items()}
        dependents = self._dependents()
        ready = [task_id for task_id, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        ordered: list[str] = []

        while ready:
            task_id = heapq.heappop(ready)
            ordered.append(task_id)
            for dependent in sorted(dependents[task_id]):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    heapq.heappush(ready, dependent)

        if len(ordered) != len(nodes):
            unresolved = sorted(task_id for task_id, degree in indegree.items() if degree > 0)
            raise ValueError(
                "task DAG contains a dependency cycle involving: " + ", ".join(unresolved)
            )
        return ordered

    def blocked_task_ids(self, *, failed_task_ids: set[str]) -> list[str]:
        """Return transitive descendants blocked by one or more failed tasks."""

        self._validate_runtime_ids(failed_task_ids, label="failed")
        dependents = self._dependents()
        blocked: set[str] = set()
        queue = deque(sorted(failed_task_ids))

        while queue:
            failed_or_blocked = queue.popleft()
            for dependent in sorted(dependents[failed_or_blocked]):
                if dependent in failed_task_ids or dependent in blocked:
                    continue
                blocked.add(dependent)
                queue.append(dependent)

        return [task_id for task_id in self.topological_order() if task_id in blocked]

    def ready_task_ids(
        self,
        *,
        completed_task_ids: set[str],
        failed_task_ids: set[str],
    ) -> list[str]:
        """Return tasks whose dependencies are completed and which are not failed/blocked."""

        self._validate_runtime_ids(completed_task_ids, label="completed")
        self._validate_runtime_ids(failed_task_ids, label="failed")
        overlap = completed_task_ids & failed_task_ids
        if overlap:
            raise ValueError(
                "task ids cannot be both completed and failed: " + ", ".join(sorted(overlap))
            )

        blocked = set(self.blocked_task_ids(failed_task_ids=failed_task_ids))
        inconsistent = completed_task_ids & blocked
        if inconsistent:
            raise ValueError(
                "completed tasks cannot be downstream of failed tasks: "
                + ", ".join(sorted(inconsistent))
            )

        nodes = self._nodes_by_id()
        ready: list[str] = []
        for task_id in self.topological_order():
            if task_id in completed_task_ids or task_id in failed_task_ids or task_id in blocked:
                continue
            if set(nodes[task_id].depends_on).issubset(completed_task_ids):
                ready.append(task_id)
        return ready

    def _nodes_by_id(self) -> dict[str, TaskNode]:
        return {node.task.task_id: node for node in self.tasks}

    def _dependents(self) -> dict[str, set[str]]:
        dependents = {task_id: set() for task_id in self.task_ids}
        for node in self.tasks:
            for dependency in node.depends_on:
                dependents[dependency].add(node.task.task_id)
        return dependents

    def _validate_runtime_ids(self, task_ids: set[str], *, label: str) -> None:
        unknown = task_ids - set(self.task_ids)
        if unknown:
            raise ValueError(f"unknown {label} task ids: {', '.join(sorted(unknown))}")
