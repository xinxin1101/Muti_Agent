from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_OID_PATTERN = r"^[0-9a-fA-F]{40,64}$"
_MODE_PATTERN = r"^[0-7]{6}$"


class MergeConflictStageSide(StrEnum):
    BASE = "base"
    INTEGRATION = "integration"
    TASK = "task"


_STAGE_SIDE = {
    1: MergeConflictStageSide.BASE,
    2: MergeConflictStageSide.INTEGRATION,
    3: MergeConflictStageSide.TASK,
}


class MergeConflictStage(BaseModel):
    """One higher-order Git index stage reported by merge-tree for a conflicted path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: Literal[1, 2, 3]
    side: MergeConflictStageSide
    mode: str = Field(pattern=_MODE_PATTERN)
    object_id: str = Field(pattern=_OID_PATTERN)

    @model_validator(mode="after")
    def validate_stage_side(self) -> MergeConflictStage:
        if self.side is not _STAGE_SIDE[self.stage]:
            raise ValueError("conflict stage side must match Git stage semantics")
        return self


class MergeConflictFile(BaseModel):
    """Structured stage evidence for one conflicted repository path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=4096)
    stages: tuple[MergeConflictStage, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def validate_stage_order(self) -> MergeConflictFile:
        stage_numbers = tuple(stage.stage for stage in self.stages)
        if len(stage_numbers) != len(set(stage_numbers)):
            raise ValueError("conflict file stages must be unique")
        if stage_numbers != tuple(sorted(stage_numbers)):
            raise ValueError("conflict file stages must be ordered by stage number")
        return self


class MergeConflictMessage(BaseModel):
    """Machine-readable merge-tree message record plus bounded human detail."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    conflict_type: str = Field(min_length=1, max_length=256)
    paths: tuple[str, ...] = Field(default_factory=tuple)
    message: str = Field(min_length=1, max_length=4096)


class MergeConflictEvidence(BaseModel):
    """Deterministic classification of one reproducible Git merge conflict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    integration_head: str = Field(pattern=_OID_PATTERN)
    task_commit: str = Field(pattern=_OID_PATTERN)
    conflict_ref: str = Field(min_length=1, max_length=512)
    marker_commit: str = Field(pattern=_OID_PATTERN)
    conflicted_tree: str = Field(pattern=_OID_PATTERN)
    git_exit_code: Literal[1] = 1
    conflicting_paths: tuple[str, ...] = Field(min_length=1)
    conflict_types: tuple[str, ...] = Field(default_factory=tuple)
    files: tuple[MergeConflictFile, ...] = Field(min_length=1)
    messages: tuple[MergeConflictMessage, ...] = Field(default_factory=tuple)
    raw_git_evidence: str = Field(min_length=1, max_length=16_000)
    raw_git_evidence_truncated: bool = False

    @model_validator(mode="after")
    def validate_evidence_consistency(self) -> MergeConflictEvidence:
        file_paths = tuple(file.path for file in self.files)
        if len(file_paths) != len(set(file_paths)):
            raise ValueError("conflict files must have unique paths")
        if self.conflicting_paths != file_paths:
            raise ValueError("conflicting_paths must exactly match conflict file order")
        if len(self.conflict_types) != len(set(self.conflict_types)):
            raise ValueError("conflict_types must be unique")
        message_types = {message.conflict_type for message in self.messages}
        if any(conflict_type not in message_types for conflict_type in self.conflict_types):
            raise ValueError("conflict_types must be backed by merge-tree message records")
        return self
