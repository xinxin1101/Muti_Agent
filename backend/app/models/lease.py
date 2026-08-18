from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TaskLeaseState(StrEnum):
    """Observed liveness state for one persisted task execution lease."""

    UNOWNED = "UNOWNED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class TaskLeaseSnapshot(BaseModel):
    """Database-time-derived ownership/liveness evidence for one task.

    `generation` is monotonic per task execution ownership transfer. The current `run_token` is
    deliberately omitted from snapshots because it is a fencing capability, not observability
    payload. EXPIRED means the recorded generation is abandoned and may be replaced by a newer
    generation; writes from the expired generation must still fail closed at fenced boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    state: TaskLeaseState
    generation: int = Field(ge=0)
    owner_id: str | None = Field(default=None, min_length=1, max_length=255)
    dispatch_id: UUID | None = None
    acquired_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_until: datetime | None = None
    released_at: datetime | None = None
    observed_at: datetime

    @property
    def abandoned(self) -> bool:
        return self.state is TaskLeaseState.EXPIRED

    @model_validator(mode="after")
    def validate_shape(self) -> TaskLeaseSnapshot:
        owned_values = (
            self.owner_id,
            self.dispatch_id,
            self.acquired_at,
            self.heartbeat_at,
            self.lease_until,
        )
        if self.state is TaskLeaseState.UNOWNED:
            if self.generation != 0:
                raise ValueError("UNOWNED leases must use generation zero")
            if any(value is not None for value in (*owned_values, self.released_at)):
                raise ValueError("UNOWNED leases must not contain ownership timestamps or identity")
            return self

        if self.generation < 1:
            raise ValueError("owned lease states require a positive generation")
        if any(value is None for value in owned_values):
            raise ValueError(
                "owned lease states require owner, dispatch, acquire, heartbeat and expiry"
            )
        assert self.lease_until is not None
        assert self.heartbeat_at is not None
        assert self.acquired_at is not None

        if self.heartbeat_at < self.acquired_at:
            raise ValueError("lease heartbeat cannot precede acquisition")
        if self.lease_until <= self.heartbeat_at:
            raise ValueError("lease expiry must follow the latest heartbeat")

        if self.state is TaskLeaseState.RELEASED:
            if self.released_at is None:
                raise ValueError("RELEASED leases require released_at")
            if self.released_at < self.acquired_at:
                raise ValueError("lease release cannot precede acquisition")
            return self

        if self.released_at is not None:
            raise ValueError("ACTIVE and EXPIRED leases must not contain released_at")
        if self.state is TaskLeaseState.ACTIVE and self.lease_until <= self.observed_at:
            raise ValueError("ACTIVE leases must expire after observed_at")
        if self.state is TaskLeaseState.EXPIRED and self.lease_until > self.observed_at:
            raise ValueError("EXPIRED leases must expire at or before observed_at")
        return self


class TaskLeaseGrant(BaseModel):
    """One live ownership grant returned only to the acquiring worker process.

    `run_token` is intentionally excluded from model serialization/repr so it does not become a
    queue payload or routine observability field. Runtime code passes it explicitly to fenced
    mutable boundaries.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot: TaskLeaseSnapshot
    run_token: UUID = Field(exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_live_grant(self) -> TaskLeaseGrant:
        if self.snapshot.state is not TaskLeaseState.ACTIVE:
            raise ValueError("task lease grants require an ACTIVE snapshot")
        if self.snapshot.generation < 1:
            raise ValueError("task lease grants require a positive generation")
        return self
