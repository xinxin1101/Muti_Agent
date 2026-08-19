from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GitHubPublicationSourceBasis(StrEnum):
    INTEGRATION = "INTEGRATION"
    SINGLE_TASK = "SINGLE_TASK"


class GitHubPublicationState(StrEnum):
    READY = "READY"
    FAILED = "FAILED"
    PUBLISHED = "PUBLISHED"


class GitHubPublicationIntent(BaseModel):
    """Immutable, evidence-bound publication target with no credential material."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    project_id: UUID
    repository_url: str = Field(min_length=1, max_length=2000)
    repository_slug: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_branch: str = Field(min_length=1, max_length=255)
    branch_name: str = Field(pattern=r"^devflow/run-[0-9a-f-]{36}$", max_length=255)
    source_basis: GitHubPublicationSourceBasis
    source_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    source_evidence_id: int = Field(ge=1)
    source_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_branch_boundary(self) -> GitHubPublicationIntent:
        if self.branch_name == self.base_branch:
            raise ValueError("DevFlow publication branch must differ from the default branch")
        return self


class GitHubRemotePullRequest(BaseModel):
    """Validated remote PR facts returned by the GitHub publication gateway."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int = Field(ge=1)
    html_url: str = Field(min_length=1, max_length=2000)
    state: str = Field(pattern=r"^(open|closed)$")
    draft: bool
    head_branch: str = Field(min_length=1, max_length=255)
    head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    base_branch: str = Field(min_length=1, max_length=255)


class PersistedGitHubPublication(BaseModel):
    """Non-authoritative audit projection for terminal-run GitHub publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: GitHubPublicationIntent
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: GitHubPublicationState
    attempt_count: int = Field(ge=0)
    pull_request_number: int | None = Field(default=None, ge=1)
    pull_request_url: str | None = Field(default=None, max_length=2000)
    pull_request_state: str | None = Field(default=None, pattern=r"^(open|closed)$")
    pull_request_draft: bool | None = None
    last_error_code: str | None = Field(default=None, max_length=64)
    last_error_message: str | None = Field(default=None, max_length=512)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_state_shape(self) -> PersistedGitHubPublication:
        pr_fields = (
            self.pull_request_number,
            self.pull_request_url,
            self.pull_request_state,
            self.pull_request_draft,
        )
        if self.state is GitHubPublicationState.PUBLISHED:
            if any(value is None for value in pr_fields):
                raise ValueError("published GitHub publication requires Pull Request facts")
            if self.last_error_code is not None or self.last_error_message is not None:
                raise ValueError("published GitHub publication must not retain failure state")
            return self

        if any(value is not None for value in pr_fields):
            raise ValueError("non-published GitHub publication must not contain Pull Request facts")
        if self.state is GitHubPublicationState.FAILED:
            if self.last_error_code is None or self.last_error_message is None:
                raise ValueError("failed GitHub publication requires bounded failure evidence")
        elif self.last_error_code is not None or self.last_error_message is not None:
            raise ValueError("ready GitHub publication must not contain failure evidence")
        return self
