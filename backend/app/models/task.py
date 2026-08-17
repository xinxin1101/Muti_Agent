from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _normalize_scope_pattern(value: str) -> str:
    pattern = value.strip()
    if not pattern:
        raise ValueError("scope patterns must not be empty")
    if pattern.startswith(("/", "\\")):
        raise ValueError("scope patterns must be repository-relative")
    if "\\" in pattern:
        raise ValueError("scope patterns must use POSIX-style '/' separators")
    if len(pattern) >= 2 and pattern[1] == ":":
        raise ValueError("scope patterns must not contain Windows drive prefixes")
    if any(part == ".." for part in pattern.split("/")):
        raise ValueError("scope patterns must not traverse outside the repository")
    if pattern == ".":
        raise ValueError("scope patterns must identify files or subdirectories")
    return pattern


def _normalize_non_empty_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized


class TaskContract(BaseModel):
    """Validated execution contract produced by planning and consumed by runtime workers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    objective: str = Field(min_length=1, max_length=4000)
    readable_files: list[str] = Field(default_factory=list)
    writable_files: list[str] = Field(min_length=1)
    readonly_files: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    verification_commands: list[str] = Field(min_length=1)
    max_retries: int = Field(default=2, ge=0, le=5)

    @field_validator("objective")
    @classmethod
    def normalize_objective(cls, value: str) -> str:
        return _normalize_non_empty_text(value)

    @field_validator("readable_files", "writable_files", "readonly_files")
    @classmethod
    def validate_scope_patterns(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_scope_pattern(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("scope patterns must not contain duplicates")
        return normalized

    @field_validator("acceptance_criteria", "verification_commands")
    @classmethod
    def validate_non_empty_items(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_non_empty_text(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("list items must not contain duplicates")
        return normalized

    @model_validator(mode="after")
    def validate_scope_boundaries(self) -> TaskContract:
        overlap = set(self.writable_files) & set(self.readonly_files)
        if overlap:
            joined = ", ".join(sorted(overlap))
            raise ValueError(f"writable_files and readonly_files overlap: {joined}")
        return self
