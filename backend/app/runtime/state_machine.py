from __future__ import annotations

from app.models.run import RunEvent, TaskRunState

_ALLOWED_TRANSITIONS: dict[TaskRunState, set[TaskRunState]] = {
    TaskRunState.PENDING: {TaskRunState.RUNNING, TaskRunState.FAILED},
    TaskRunState.RUNNING: {TaskRunState.VERIFYING, TaskRunState.FAILED},
    TaskRunState.VERIFYING: {
        TaskRunState.REVIEWING,
        TaskRunState.REPAIRING,
        TaskRunState.FAILED,
    },
    TaskRunState.REVIEWING: {
        TaskRunState.SUCCEEDED,
        TaskRunState.REPAIRING,
        TaskRunState.FAILED,
    },
    TaskRunState.REPAIRING: {TaskRunState.VERIFYING, TaskRunState.FAILED},
    TaskRunState.SUCCEEDED: set(),
    TaskRunState.FAILED: set(),
}


class TaskStateMachine:
    """Small explicit state machine for the V0.1 single-task runtime."""

    def __init__(self) -> None:
        self._state = TaskRunState.PENDING
        self._events = [
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Task run created.")
        ]

    @property
    def state(self) -> TaskRunState:
        return self._state

    @property
    def events(self) -> list[RunEvent]:
        return list(self._events)

    def transition(self, next_state: TaskRunState, *, detail: str) -> None:
        allowed = _ALLOWED_TRANSITIONS[self._state]
        if next_state not in allowed:
            raise ValueError(
                f"invalid task-state transition: {self._state.value} -> {next_state.value}"
            )
        self._state = next_state
        self._events.append(RunEvent(sequence=len(self._events), state=next_state, detail=detail))
