from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskScheduleState(StrEnum):
    """Lifecycle state for one task inside the V0.2 DAG scheduler."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class TaskScheduleRecord(BaseModel):
    """Immutable task-state record exposed by scheduler snapshots."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    state: TaskScheduleState


class SchedulerEvent(BaseModel):
    """One auditable scheduler transition for a DAG task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=0)
    task_id: str = Field(min_length=1, max_length=128)
    from_state: TaskScheduleState
    to_state: TaskScheduleState
    detail: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def reject_noop_transition(self) -> SchedulerEvent:
        if self.from_state is self.to_state:
            raise ValueError("scheduler events must represent a real state transition")
        return self


class SchedulerSnapshot(BaseModel):
    """Immutable deterministic view of the complete DAG scheduling state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: tuple[TaskScheduleRecord, ...]
    events: tuple[SchedulerEvent, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_unique_task_ids(self) -> SchedulerSnapshot:
        task_ids = [record.task_id for record in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("scheduler snapshot must contain each task exactly once")
        return self
