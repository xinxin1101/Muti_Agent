from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DispatchAttemptState(StrEnum):
    """Durable publication observation for one stable dispatch identity."""

    REQUESTED = "REQUESTED"
    ENQUEUED = "ENQUEUED"
    PUBLISH_FAILED = "PUBLISH_FAILED"


class PersistedDispatchAttempt(BaseModel):
    """Bounded PostgreSQL truth around one broker publication attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dispatch_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    attempt_number: int = Field(ge=1)
    state: DispatchAttemptState
    broker_message_id: str | None = Field(default=None, min_length=1, max_length=128)
    queue_name: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, min_length=1, max_length=64)
    error_message: str | None = Field(default=None, min_length=1, max_length=512)
    requested_at: datetime
    resolved_at: datetime | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def validate_state_shape(self) -> PersistedDispatchAttempt:
        if self.updated_at < self.requested_at:
            raise ValueError("dispatch attempt updated_at cannot precede requested_at")
        if self.resolved_at is not None and self.resolved_at < self.requested_at:
            raise ValueError("dispatch attempt resolved_at cannot precede requested_at")

        broker_values = (self.broker_message_id, self.queue_name)
        failure_values = (self.error_code, self.error_message)

        if self.state is DispatchAttemptState.REQUESTED:
            if any(value is not None for value in (*broker_values, *failure_values)):
                raise ValueError("REQUESTED dispatch attempts cannot claim a publication outcome")
            if self.resolved_at is not None:
                raise ValueError("REQUESTED dispatch attempts must remain unresolved")
            return self

        if self.resolved_at is None:
            raise ValueError("resolved dispatch attempts require resolved_at")

        if self.state is DispatchAttemptState.ENQUEUED:
            if any(value is None for value in broker_values):
                raise ValueError("ENQUEUED dispatch attempts require broker acknowledgement facts")
            if any(value is not None for value in failure_values):
                raise ValueError("ENQUEUED dispatch attempts cannot retain failure facts")
            return self

        if any(value is not None for value in broker_values):
            raise ValueError("PUBLISH_FAILED attempts cannot claim broker acknowledgement facts")
        if any(value is None for value in failure_values):
            raise ValueError("PUBLISH_FAILED attempts require bounded error facts")
        return self
