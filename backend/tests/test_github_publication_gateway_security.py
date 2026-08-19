from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import SecretStr

from app.models.publication import GitHubPublicationIntent, GitHubPublicationSourceBasis
from app.publication import GitHubPublicationGateway, GitHubPublicationGatewayError

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")


def _intent() -> GitHubPublicationIntent:
    return GitHubPublicationIntent(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        repository_slug="example/repo",
        base_branch="main",
        branch_name=f"devflow/run-{RUN_ID}",
        source_basis=GitHubPublicationSourceBasis.SINGLE_TASK,
        source_commit="a" * 40,
        source_evidence_id=7,
        source_evidence_sha256="b" * 64,
    )


def test_github_api_token_destination_is_not_configurable() -> None:
    with pytest.raises(ValueError, match="exactly https://api.github.com"):
        GitHubPublicationGateway(
            SecretStr("backend-token"),
            api_base_url="https://attacker.example",
        )

    gateway = GitHubPublicationGateway(
        SecretStr("backend-token"),
        api_base_url="https://api.github.com/",
    )
    assert gateway._api_base_url == "https://api.github.com"  # noqa: SLF001


def test_pull_request_url_must_match_exact_remote_number() -> None:
    intent = _intent()
    payload = {
        "number": 42,
        "html_url": "https://github.com/example/repo/pull/420",
        "state": "open",
        "draft": True,
        "head": {"ref": intent.branch_name, "sha": intent.source_commit},
        "base": {"ref": intent.base_branch},
    }

    with pytest.raises(GitHubPublicationGatewayError, match="PR number") as raised:
        GitHubPublicationGateway._decode_pull_request(payload, intent)  # noqa: SLF001
    assert raised.value.code == "REMOTE_PR_IDENTITY_MISMATCH"
