from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import TokenUsage
from app.models.failure import FailureSource, FailureType


class RepairFailureKind(StrEnum):
    IMPORT_SYMBOL_MISSING = "IMPORT_SYMBOL_MISSING"
    PYTHON_ATTRIBUTE_MISSING = "PYTHON_ATTRIBUTE_MISSING"


class RepairFailureDigest(BaseModel):
    """Bounded failure facts handed to a fresh Repair session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_type: FailureType
    source: FailureSource
    message: str = Field(min_length=1, max_length=2_000)
    evidence: tuple[str, ...] = Field(default_factory=tuple, max_length=8)


class RepairHandoff(BaseModel):
    """Issue-scoped repair input with no Developer conversation or source dump."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=4_000)
    repository_head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    acceptance_criteria: tuple[str, ...] = Field(min_length=1, max_length=32)
    verification_commands: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    writable_files: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    readonly_files: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    changed_files: tuple[str, ...] = Field(default_factory=tuple, max_length=256)
    relevant_paths: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    failure_kind: RepairFailureKind | None = None
    suspected_path: str | None = Field(default=None, max_length=500)
    suspected_symbol: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        max_length=256,
    )
    suspected_member: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        max_length=256,
    )
    failures: tuple[RepairFailureDigest, ...] = Field(min_length=1, max_length=8)


class RepairStopReason(StrEnum):
    MODEL_STOP = "MODEL_STOP"
    NO_PROGRESS = "NO_PROGRESS"
    EXPLICIT_BLOCKER = "EXPLICIT_BLOCKER"
    ITERATION_LIMIT = "ITERATION_LIMIT"
    TIME_LIMIT = "TIME_LIMIT"
    TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"


class RepairProgressStatus(StrEnum):
    """Deterministic outcome of one repair attempt, independent of agent claims."""

    PATCH_PRODUCED = "PATCH_PRODUCED"
    NO_PATCH_PRODUCED = "NO_PATCH_PRODUCED"
    PROGRESS_MADE = "PROGRESS_MADE"
    REPAIR_INEFFECTIVE = "REPAIR_INEFFECTIVE"
    REPAIRED = "REPAIRED"


class RepairProgressEvidence(BaseModel):
    """Workspace and verification evidence captured around one Repair Agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: RepairProgressStatus
    has_patch: bool
    files_changed: list[str] = Field(default_factory=list)
    patch_hash_before: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_hash_after: str = Field(pattern=r"^[0-9a-f]{64}$")
    failure_signature_before: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_signature_after: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    failure_stage_before: str | None = Field(default=None, max_length=1_000)
    failure_stage_after: str | None = Field(default=None, max_length=1_000)
    validation_executed: bool = False
    validation_commands: list[str] = Field(default_factory=list)


class RepairRunResult(BaseModel):
    """Evidence from one targeted repair attempt; this is not a success verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int = Field(ge=1)
    failure_types: list[FailureType] = Field(min_length=1)
    stop_reason: RepairStopReason
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_message: str = ""
    changed_files: list[str] = Field(default_factory=list)
    progress: RepairProgressEvidence | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(default=0, ge=0)
