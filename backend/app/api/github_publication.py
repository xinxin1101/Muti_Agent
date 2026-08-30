from __future__ import annotations

import inspect
from typing import Protocol
from uuid import UUID

from app.api.models import ProductGitHubPublication
from app.api.publication import (
    ProductGitHubPublicationUnavailableError,
    build_product_publication,
    resolve_github_publication_intent,
)
from app.api.service import ProductRuntimeService
from app.models.publication import (
    GitHubPublicationIntent,
    GitHubPublicationState,
    GitHubRemotePullRequest,
    PersistedGitHubPublication,
)
from app.persistence.serialization import canonical_payload
from app.publication import GitHubPublicationGatewayError
from app.workspace import WorkspaceGitError


class ProductGitHubPublicationConfigurationError(RuntimeError):
    """Raised when publication is eligible but no backend GitHub credential is configured."""


class ProductGitHubPublicationFailedError(RuntimeError):
    """Bounded external publication failure that never changes Run truth."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


class ProductGitHubPublicationStore(Protocol):
    async def load(self, run_id: UUID) -> PersistedGitHubPublication | None: ...
    async def begin_attempt(
        self,
        intent: GitHubPublicationIntent,
    ) -> tuple[PersistedGitHubPublication, UUID | None]: ...
    async def mark_published(
        self,
        *,
        run_id: UUID,
        intent_sha256: str,
        attempt_token: UUID,
        remote: GitHubRemotePullRequest,
    ) -> PersistedGitHubPublication: ...
    async def mark_failed(
        self,
        *,
        run_id: UUID,
        intent_sha256: str,
        attempt_token: UUID,
        error_code: str,
        error_message: str,
    ) -> PersistedGitHubPublication: ...
    async def dispose(self) -> None: ...


class ProductGitHubPublisher(Protocol):
    async def is_configured(self, project_id: UUID) -> bool: ...

    async def publish(
        self,
        *,
        workspace,
        intent: GitHubPublicationIntent,
        title: str,
        body: str,
    ) -> GitHubRemotePullRequest: ...
    async def dispose(self) -> None: ...


class ProductRuntimeServiceWithGitHubPublication(ProductRuntimeService):
    """Product facade extension that publishes only already-accepted Git/runtime truth."""

    def __init__(
        self,
        *,
        publication_store: ProductGitHubPublicationStore,
        github_publisher: ProductGitHubPublisher | None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._publication_store = publication_store
        self._github_publisher = github_publisher

    async def dispose(self) -> None:
        await super().dispose()
        dispatcher_dispose = getattr(self._dispatcher, "dispose", None)
        if dispatcher_dispose is not None:
            await dispatcher_dispose()
        await self._publication_store.dispose()
        if self._github_publisher is not None:
            await self._github_publisher.dispose()

    async def get_github_publication(self, run_id: UUID) -> ProductGitHubPublication:
        snapshot = await self._evidence_store.load_run(run_id)
        workspace = self._resolve_publication_workspace(snapshot.project_id)
        intent = resolve_github_publication_intent(snapshot, workspace)
        persisted = await self._publication_store.load(run_id)
        return build_product_publication(
            intent,
            persisted,
            publisher_configured=await self._publisher_is_configured(intent.project_id),
        )

    async def publish_github_draft(self, run_id: UUID) -> ProductGitHubPublication:
        snapshot = await self._evidence_store.load_run(run_id)
        workspace = self._resolve_publication_workspace(snapshot.project_id)
        intent = resolve_github_publication_intent(snapshot, workspace)
        persisted = await self._publication_store.load(run_id)
        current = build_product_publication(
            intent,
            persisted,
            publisher_configured=await self._publisher_is_configured(intent.project_id),
        )
        if current.state is GitHubPublicationState.PUBLISHED:
            return current
        if self._github_publisher is None or not await self._publisher_is_configured(
            intent.project_id
        ):
            raise ProductGitHubPublicationConfigurationError(
                "GitHub publication is not configured on the backend"
            )

        attempt, attempt_token = await self._publication_store.begin_attempt(intent)
        if attempt.state is GitHubPublicationState.PUBLISHED:
            return build_product_publication(
                intent,
                attempt,
                publisher_configured=True,
            )
        if attempt_token is None:
            raise RuntimeError("active GitHub publication attempt is missing its backend claim")
        _, intent_sha256 = canonical_payload(intent)

        try:
            remote = await self._github_publisher.publish(
                workspace=workspace,
                intent=intent,
                title=f"DevFlow Run {run_id}",
                body=self._pull_request_body(intent),
            )
        except GitHubPublicationGatewayError as exc:
            failed = await self._publication_store.mark_failed(
                run_id=run_id,
                intent_sha256=intent_sha256,
                attempt_token=attempt_token,
                error_code=exc.code,
                error_message=exc.public_message,
            )
            if failed.state is GitHubPublicationState.PUBLISHED:
                return build_product_publication(
                    intent,
                    failed,
                    publisher_configured=True,
                )
            raise ProductGitHubPublicationFailedError(
                exc.code,
                exc.public_message,
            ) from exc

        published = await self._publication_store.mark_published(
            run_id=run_id,
            intent_sha256=intent_sha256,
            attempt_token=attempt_token,
            remote=remote,
        )
        return build_product_publication(
            intent,
            published,
            publisher_configured=True,
        )

    def _resolve_publication_workspace(self, project_id: UUID):
        try:
            return self._workspace_resolver.resolve(project_id)
        except (ValueError, WorkspaceGitError) as exc:
            raise ProductGitHubPublicationUnavailableError(
                "managed Git workspace is unavailable or untrustworthy for publication"
            ) from exc

    async def _publisher_is_configured(self, project_id: UUID) -> bool:
        if self._github_publisher is None:
            return False
        configured = getattr(self._github_publisher, "is_configured", None)
        if not callable(configured):
            return True
        result = configured(project_id)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    @staticmethod
    def _pull_request_body(intent: GitHubPublicationIntent) -> str:
        return "\n".join(
            (
                "## DevFlow publication",
                "",
                "This Draft Pull Request publishes code already accepted by "
                "DevFlow runtime evidence.",
                "GitHub state does not authorize verification, review, integration, "
                "or Run success.",
                "",
                f"- Run: `{intent.run_id}`",
                f"- Source basis: `{intent.source_basis.value}`",
                f"- Source commit: `{intent.source_commit}`",
                f"- Evidence id: `{intent.source_evidence_id}`",
                f"- Evidence SHA-256: `{intent.source_evidence_sha256}`",
                f"- Base branch: `{intent.base_branch}`",
                f"- DevFlow branch: `{intent.branch_name}`",
            )
        )
