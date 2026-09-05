from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import TokenUsage
from app.models.failure import FailureSource, FailureType

_REVIEW_ISSUE_EVIDENCE_RE = re.compile(
    r"^issue_\d+=severity:(?:low|medium|high|critical);location:(?P<location>.*?);message:"
)


class RepairFailureKind(StrEnum):
    IMPORT_SYMBOL_MISSING = "IMPORT_SYMBOL_MISSING"
    PYTHON_ATTRIBUTE_MISSING = "PYTHON_ATTRIBUTE_MISSING"
    SEMANTIC_REVIEW_ISSUE = "SEMANTIC_REVIEW_ISSUE"


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
    suspected_line: int | None = Field(default=None, ge=1)
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

    @model_validator(mode="before")
    @classmethod
    def derive_semantic_review_target(cls, data: Any) -> Any:
        """Recover Reviewer file/line only from DevFlow's fixed REVIEW_REJECTED evidence shape."""

        if not isinstance(data, dict):
            return data
        if (
            data.get("failure_kind") is not None
            or data.get("suspected_path") is not None
            or data.get("suspected_line") is not None
        ):
            return data

        target = cls._semantic_review_target(data.get("failures"))
        if target is None:
            return data

        path, line = target
        normalized = dict(data)
        normalized["failure_kind"] = RepairFailureKind.SEMANTIC_REVIEW_ISSUE
        normalized["suspected_path"] = path
        normalized["suspected_line"] = line

        relevant_paths = list(normalized.get("relevant_paths") or ())
        if path not in relevant_paths and len(relevant_paths) < 16:
            relevant_paths.append(path)
            normalized["relevant_paths"] = tuple(relevant_paths)
        return normalized

    @classmethod
    def _semantic_review_target(cls, failures: Any) -> tuple[str, int | None] | None:
        if not isinstance(failures, (list, tuple)):
            return None
        for failure in failures:
            if isinstance(failure, RepairFailureDigest):
                failure_type = failure.failure_type
                source = failure.source
                evidence = failure.evidence
            elif isinstance(failure, dict):
                failure_type = failure.get("failure_type")
                source = failure.get("source")
                evidence = failure.get("evidence") or ()
            else:
                continue
            if (
                failure_type not in {FailureType.REVIEW_REJECTED, FailureType.REVIEW_REJECTED.value}
                or source not in {FailureSource.REVIEW, FailureSource.REVIEW.value}
                or not isinstance(evidence, (list, tuple))
            ):
                continue
            for item in evidence:
                if not isinstance(item, str):
                    continue
                match = _REVIEW_ISSUE_EVIDENCE_RE.match(item)
                if match is None:
                    continue
                parsed = cls._parse_review_location(match.group("location"))
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _parse_review_location(location: str) -> tuple[str, int | None] | None:
        normalized = location.strip()
        if not normalized or normalized == "unknown":
            return None

        path = normalized
        line: int | None = None
        candidate_path, separator, candidate_line = normalized.rpartition(":")
        if separator and candidate_line.isdigit():
            parsed_line = int(candidate_line)
            if parsed_line < 1:
                return None
            path = candidate_path
            line = parsed_line

        path = path.strip()
        if not RepairHandoff._is_safe_repository_path(path):
            return None
        return path, line

    @staticmethod
    def _is_safe_repository_path(path: str) -> bool:
        if not path or len(path) > 500:
            return False
        if path.startswith(("/", "\\")) or "\\" in path:
            return False
        if len(path) >= 2 and path[1] == ":":
            return False
        if path == "." or any(part == ".." for part in path.split("/")):
            return False
        return True


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
