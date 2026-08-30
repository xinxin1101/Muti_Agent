from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr

from app.models.publication import GitHubPublicationIntent, GitHubRemotePullRequest
from app.persistence.credentials import (
    PostgresProjectCredentialStore,
    ProjectCredentialConfigurationError,
    ProjectCredentialDecryptionError,
)
from app.publication.github import GitHubPublicationGateway, GitHubPublicationGatewayError
from app.workspace import LocalGitWorkspace


class PersistentProjectGitHubPublisher:
    """Publish using a project credential encrypted in PostgreSQL or a global fallback."""

    def __init__(
        self,
        *,
        credential_store: PostgresProjectCredentialStore,
        fallback_token: SecretStr | None = None,
    ) -> None:
        self._credential_store = credential_store
        self._fallback_token = fallback_token

    async def remember(self, project_id: UUID, token: SecretStr | None) -> None:
        if token is None or not token.get_secret_value().strip():
            return
        await self._credential_store.save_github_publication_token(project_id, token)

    async def is_configured(self, project_id: UUID) -> bool:
        if self._fallback_token is not None:
            return True
        return await self._credential_store.has_github_publication_token(project_id)

    async def publish(
        self,
        *,
        workspace: LocalGitWorkspace,
        intent: GitHubPublicationIntent,
        title: str,
        body: str,
    ) -> GitHubRemotePullRequest:
        token = None
        if self._credential_store.enabled:
            try:
                token = await self._credential_store.load_github_publication_token(
                    intent.project_id
                )
            except (ProjectCredentialConfigurationError, ProjectCredentialDecryptionError) as exc:
                raise GitHubPublicationGatewayError(
                    "PROJECT_CREDENTIAL_UNAVAILABLE",
                    "Saved project GitHub credential is unavailable. Register the project again.",
                ) from exc
        token = token or self._fallback_token
        if token is None:
            raise GitHubPublicationGatewayError(
                "PROJECT_CREDENTIAL_UNAVAILABLE",
                "No GitHub publication credential is configured for this project.",
            )
        gateway = GitHubPublicationGateway(token)
        try:
            return await gateway.publish(
                workspace=workspace,
                intent=intent,
                title=title,
                body=body,
            )
        finally:
            await gateway.dispose()

    async def dispose(self) -> None:
        await self._credential_store.dispose()
