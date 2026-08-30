from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InterfaceContractState(StrEnum):
    DECLARED = "DECLARED"
    SATISFIED = "SATISFIED"
    UNMET = "UNMET"


class InterfaceContractGate(BaseModel):
    """A durable dispatch decision; a denied gate never authorizes an Agent call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    allowed: bool
    missing_interfaces: tuple[str, ...] = ()
    producer_tasks: tuple[str, ...] = ()
    reason: str | None = Field(default=None, max_length=512)
