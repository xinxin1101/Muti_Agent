from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from pydantic import SecretStr

from app.publication.persistent import PersistentProjectGitHubPublisher


class _CredentialStore:
    def __init__(self) -> None:
        self.enabled = True
        self.tokens: dict[UUID, SecretStr] = {}
        self.disposed = False

    async def save_github_publication_token(self, project_id: UUID, token: SecretStr) -> None:
        self.tokens[project_id] = token

    async def has_github_publication_token(self, project_id: UUID) -> bool:
        return project_id in self.tokens

    async def load_github_publication_token(self, project_id: UUID) -> SecretStr | None:
        return self.tokens.get(project_id)

    async def dispose(self) -> None:
        self.disposed = True


def test_project_publication_token_survives_publisher_recreation() -> None:
    project_id = uuid4()
    store = _CredentialStore()
    first_process = PersistentProjectGitHubPublisher(credential_store=store)  # type: ignore[arg-type]

    asyncio.run(first_process.remember(project_id, SecretStr("test-project-token")))

    restarted_process = PersistentProjectGitHubPublisher(credential_store=store)  # type: ignore[arg-type]
    assert asyncio.run(restarted_process.is_configured(project_id)) is True
    assert asyncio.run(store.load_github_publication_token(project_id)).get_secret_value() == (
        "test-project-token"
    )
