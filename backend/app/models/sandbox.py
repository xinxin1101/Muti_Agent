from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DockerSandboxPolicy(BaseModel):
    """Immutable security/resource policy for one verification container."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str = Field(default="devflow-verifier:py311", min_length=1, max_length=512)
    cpus: float = Field(default=1.0, ge=0.05, le=32.0)
    memory_mb: int = Field(default=512, ge=64, le=32_768)
    pids_limit: int = Field(default=128, ge=16, le=2_048)
    tmpfs_mb: int = Field(default=128, ge=16, le=4_096)
    shm_mb: int = Field(default=64, ge=16, le=1_024)

    network: Literal["none"] = "none"
    pull_policy: Literal["never"] = "never"
    read_only_root: Literal[True] = True
    read_only_workspace: Literal[True] = True
    drop_all_capabilities: Literal[True] = True
    no_new_privileges: Literal[True] = True
    container_user: Literal["65532:65532"] = "65532:65532"

    @field_validator("image")
    @classmethod
    def validate_image_reference(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value or not normalized:
            raise ValueError("sandbox image must be a non-empty trimmed reference")
        if normalized.startswith("-"):
            raise ValueError("sandbox image must not begin with an option prefix")
        if any(character.isspace() or ord(character) < 32 for character in normalized):
            raise ValueError("sandbox image must not contain whitespace or control characters")
        return normalized
