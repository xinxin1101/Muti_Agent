from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuntimeEventLevel(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class RuntimeEventSource(StrEnum):
    PERSISTENCE = "PERSISTENCE"
    LEASE = "LEASE"
    DISPATCH = "DISPATCH"
    WORKER = "WORKER"
    RUNTIME = "RUNTIME"
    AGENT = "AGENT"
    VERIFICATION = "VERIFICATION"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"
    INTEGRATION = "INTEGRATION"


class RuntimeEventKind(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINALIZED = "RUN_FINALIZED"
    LEASE_ACQUIRED = "LEASE_ACQUIRED"
    LEASE_TAKEN_OVER = "LEASE_TAKEN_OVER"
    LEASE_HEARTBEAT = "LEASE_HEARTBEAT"
    LEASE_RELEASED = "LEASE_RELEASED"
    EVIDENCE_RECORDED = "EVIDENCE_RECORDED"


_SENSITIVE_ATTRIBUTE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer_token",
    "password",
    "refresh_token",
    "run_token",
    "secret",
    "siliconflow_api_key",
}
_MAX_ATTRIBUTES_BYTES = 16_384


class RuntimeEventDraft(BaseModel):
    """Structured append-only runtime event before database sequence/timestamp assignment.

    Event payloads are intentionally compact observability metadata. Large typed evidence remains in
    the evidence store, while secrets and fencing capabilities are forbidden from event attributes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_key: str = Field(min_length=1, max_length=255)
    kind: RuntimeEventKind
    source: RuntimeEventSource
    level: RuntimeEventLevel = RuntimeEventLevel.INFO
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    dispatch_id: UUID | None = None
    generation: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=1000)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_structure(self) -> RuntimeEventDraft:
        if self.task_id is None and (self.dispatch_id is not None or self.generation is not None):
            raise ValueError("dispatch_id and generation require task_id correlation")
        self._validate_attributes(self.attributes)
        try:
            encoded = json.dumps(
                self.attributes,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime event attributes must be JSON serializable") from exc
        if len(encoded) > _MAX_ATTRIBUTES_BYTES:
            raise ValueError(
                f"runtime event attributes exceed {_MAX_ATTRIBUTES_BYTES} UTF-8 bytes"
            )
        return self

    @classmethod
    def _validate_attributes(cls, value: object) -> None:
        if isinstance(value, dict):
            for raw_key, nested in value.items():
                key = str(raw_key).strip().lower()
                if key in _SENSITIVE_ATTRIBUTE_KEYS or key.endswith("_api_key"):
                    raise ValueError(f"runtime event attribute {raw_key!r} is sensitive")
                cls._validate_attributes(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                cls._validate_attributes(nested)


class PersistedRuntimeEvent(BaseModel):
    """Database-assigned immutable event returned by timeline queries."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int = Field(ge=1)
    event_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    event_key: str = Field(min_length=1, max_length=255)
    kind: RuntimeEventKind
    source: RuntimeEventSource
    level: RuntimeEventLevel
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    dispatch_id: UUID | None = None
    generation: int | None = Field(default=None, ge=1)
    message: str = Field(min_length=1, max_length=1000)
    schema_version: int = Field(ge=1)
    attributes: dict[str, Any]
    attributes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
