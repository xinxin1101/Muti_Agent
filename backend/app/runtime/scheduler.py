from __future__ import annotations

from app.models.dag import TaskDAG
from app.models.scheduler import (
    SchedulerEvent,
    SchedulerSnapshot,
    TaskScheduleRecord,
    TaskScheduleState,
)

_TERMINAL_STATES = {
    TaskScheduleState.SUCCEEDED,
    TaskScheduleState.FAILED,
    TaskScheduleState.BLOCKED,
}

_ALLOWED_TRANSITIONS: dict[TaskScheduleState, set[TaskScheduleState]] = {
    TaskScheduleState.PENDING: {
        TaskScheduleState.READY,
        TaskScheduleState.BLOCKED,
    },
    TaskScheduleState.READY: {TaskScheduleState.RUNNING},
    TaskScheduleState.RUNNING: {
        TaskScheduleState.SUCCEEDED,
        TaskScheduleState.FAILED,
    },
    TaskScheduleState.SUCCEEDED: set(),
    TaskScheduleState.FAILED: set(),
    TaskScheduleState.BLOCKED: set(),
}


class DAGScheduler:
    """Deterministic in-memory scheduler state machine for one validated TaskDAG."""

    def __init__(self, dag: TaskDAG) -> None:
        self._dag = dag
        self._states = {task_id: TaskScheduleState.PENDING for task_id in dag.task_ids}
        self._events: list[SchedulerEvent] = []
        self._refresh_ready(detail="All dependencies are satisfied at scheduler initialization.")

    @property
    def dag(self) -> TaskDAG:
        return self._dag

    @property
    def events(self) -> tuple[SchedulerEvent, ...]:
        return tuple(self._events)

    @property
    def is_terminal(self) -> bool:
        return all(state in _TERMINAL_STATES for state in self._states.values())

    def state(self, task_id: str) -> TaskScheduleState:
        return self._require_task(task_id)

    def task_ids_in_state(self, state: TaskScheduleState) -> tuple[str, ...]:
        return tuple(
            task_id for task_id in self._dag.topological_order() if self._states[task_id] is state
        )

    def ready_task_ids(self) -> tuple[str, ...]:
        return self.task_ids_in_state(TaskScheduleState.READY)

    def start(self, task_id: str) -> None:
        """Move exactly one dependency-ready task from READY to RUNNING."""

        self._require_state(task_id, TaskScheduleState.READY)
        self._transition(
            task_id,
            TaskScheduleState.RUNNING,
            detail="Task execution started.",
        )

    def succeed(self, task_id: str) -> None:
        """Mark one RUNNING task successful and unlock newly-ready dependents."""

        self._require_state(task_id, TaskScheduleState.RUNNING)
        self._transition(
            task_id,
            TaskScheduleState.SUCCEEDED,
            detail="Task execution completed successfully.",
        )
        self._refresh_ready(detail=f"Dependencies satisfied after {task_id} succeeded.")

    def fail(self, task_id: str) -> None:
        """Mark one RUNNING task failed and transitively block downstream tasks."""

        self._require_state(task_id, TaskScheduleState.RUNNING)
        failed_after_transition = self._task_ids_with_state(TaskScheduleState.FAILED) | {task_id}
        blocked_after_transition = set(
            self._dag.blocked_task_ids(failed_task_ids=failed_after_transition)
        )
        self._assert_blockable(blocked_after_transition)

        self._transition(
            task_id,
            TaskScheduleState.FAILED,
            detail="Task execution failed.",
        )
        for blocked_task_id in self._dag.topological_order():
            if blocked_task_id not in blocked_after_transition:
                continue
            if self._states[blocked_task_id] is TaskScheduleState.BLOCKED:
                continue
            self._transition(
                blocked_task_id,
                TaskScheduleState.BLOCKED,
                detail="Blocked by a failed upstream dependency.",
            )

    def snapshot(self) -> SchedulerSnapshot:
        """Return an immutable snapshot ordered by the DAG's deterministic topology."""

        return SchedulerSnapshot(
            tasks=tuple(
                TaskScheduleRecord(task_id=task_id, state=self._states[task_id])
                for task_id in self._dag.topological_order()
            ),
            events=tuple(self._events),
        )

    def _refresh_ready(self, *, detail: str) -> None:
        ready = self._dag.ready_task_ids(
            completed_task_ids=self._task_ids_with_state(TaskScheduleState.SUCCEEDED),
            failed_task_ids=self._task_ids_with_state(TaskScheduleState.FAILED),
        )
        for task_id in ready:
            if self._states[task_id] is not TaskScheduleState.PENDING:
                continue
            self._transition(task_id, TaskScheduleState.READY, detail=detail)

    def _assert_blockable(self, blocked_task_ids: set[str]) -> None:
        invalid: list[str] = []
        for task_id in sorted(blocked_task_ids):
            state = self._states[task_id]
            if state not in {
                TaskScheduleState.PENDING,
                TaskScheduleState.BLOCKED,
            }:
                invalid.append(f"{task_id}={state.value}")
        if invalid:
            raise RuntimeError(
                "scheduler invariant violation: failed dependency would block "
                "a ready/active/terminal task: " + ", ".join(invalid)
            )

    def _require_task(self, task_id: str) -> TaskScheduleState:
        try:
            return self._states[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task id: {task_id}") from exc

    def _require_state(self, task_id: str, expected: TaskScheduleState) -> None:
        current = self._require_task(task_id)
        if current is not expected:
            raise ValueError(
                f"task {task_id} must be {expected.value} before this transition; "
                f"current state is {current.value}"
            )

    def _task_ids_with_state(self, state: TaskScheduleState) -> set[str]:
        return {task_id for task_id, current in self._states.items() if current is state}

    def _transition(
        self,
        task_id: str,
        next_state: TaskScheduleState,
        *,
        detail: str,
    ) -> None:
        current = self._require_task(task_id)
        if next_state not in _ALLOWED_TRANSITIONS[current]:
            raise ValueError(
                f"invalid DAG scheduler transition: {current.value} -> {next_state.value} "
                f"for {task_id}"
            )
        self._states[task_id] = next_state
        self._events.append(
            SchedulerEvent(
                sequence=len(self._events),
                task_id=task_id,
                from_state=current,
                to_state=next_state,
                detail=detail,
            )
        )
