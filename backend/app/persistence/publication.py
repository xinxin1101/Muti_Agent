from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.publication import (
    GitHubPublicationIntent,
    GitHubPublicationState,
    GitHubRemotePullRequest,
    PersistedGitHubPublication,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.models import GitHubPublicationRow, RunRow
from app.persistence.serialization import canonical_payload, verify_payload_hash
from app.persistence.types import PersistedRunStatus


class PostgresGitHubPublicationStore:
    """Non-authoritative audit persistence for bounded GitHub publication attempts."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        echo: bool = False,
    ) -> PostgresGitHubPublicationStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def load(self, run_id: UUID) -> PersistedGitHubPublication | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(GitHubPublicationRow).where(GitHubPublicationRow.run_id == run_id)
            )
        return None if row is None else self._decode(row)

    async def begin_attempt(
        self,
        intent: GitHubPublicationIntent,
    ) -> PersistedGitHubPublication:
        payload, digest = canonical_payload(intent)
        async with self._session_factory.begin() as session:
            run = (
                await session.execute(
                    select(RunRow).where(RunRow.id == intent.run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise ValueError(f"unknown persistence run: {intent.run_id}")
            if run.project_id != intent.project_id:
                raise PersistenceCorruptionError(
                    "GitHub publication project identity disagrees with the persisted Run"
                )
            if run.status != PersistedRunStatus.SUCCEEDED.value:
                raise PersistenceConflictError(
                    "GitHub publication attempts require an already-SUCCEEDED persisted Run"
                )

            row = (
                await session.execute(
                    select(GitHubPublicationRow)
                    .where(GitHubPublicationRow.run_id == intent.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = GitHubPublicationRow(
                    run_id=intent.run_id,
                    intent=payload,
                    intent_sha256=digest,
                    state=GitHubPublicationState.READY.value,
                    attempt_count=1,
                )
                session.add(row)
                await session.flush()
                await session.refresh(row)
                return self._decode(row)

            current = self._decode(row)
            if current.intent_sha256 != digest:
                raise PersistenceConflictError(
                    "GitHub publication intent is immutable and differs from the accepted audit row"
                )
            if current.state is GitHubPublicationState.PUBLISHED:
                return current

            row.state = GitHubPublicationState.READY.value
            row.attempt_count += 1
            row.pull_request_number = None
            row.pull_request_url = None
            row.pull_request_state = None
            row.pull_request_draft = None
            row.last_error_code = None
            row.last_error_message = None
            row.updated_at = await self._database_time(session)
            await session.flush()
            await session.refresh(row)
            return self._decode(row)

    async def mark_published(
        self,
        *,
        run_id: UUID,
        intent_sha256: str,
        remote: GitHubRemotePullRequest,
    ) -> PersistedGitHubPublication:
        async with self._session_factory.begin() as session:
            row = await self._locked_row(session, run_id)
            current = self._decode(row)
            self._assert_intent_digest(current, intent_sha256)

            if current.state is GitHubPublicationState.PUBLISHED:
                if (
                    current.pull_request_number != remote.number
                    or current.pull_request_url != remote.html_url
                    or current.pull_request_state != remote.state
                    or current.pull_request_draft != remote.draft
                ):
                    raise PersistenceConflictError(
                        "published GitHub audit facts cannot be replaced by different remote facts"
                    )
                return current

            intent = current.intent
            if (
                remote.head_branch != intent.branch_name
                or remote.head_commit != intent.source_commit
                or remote.base_branch != intent.base_branch
            ):
                raise PersistenceConflictError(
                    "GitHub Pull Request facts do not match the immutable publication intent"
                )

            row.state = GitHubPublicationState.PUBLISHED.value
            row.pull_request_number = remote.number
            row.pull_request_url = remote.html_url
            row.pull_request_state = remote.state
            row.pull_request_draft = remote.draft
            row.last_error_code = None
            row.last_error_message = None
            row.updated_at = await self._database_time(session)
            await session.flush()
            await session.refresh(row)
            return self._decode(row)

    async def mark_failed(
        self,
        *,
        run_id: UUID,
        intent_sha256: str,
        error_code: str,
        error_message: str,
    ) -> PersistedGitHubPublication:
        code = error_code.strip()[:64]
        message = error_message.strip()[:512]
        if not code or not message:
            raise ValueError("GitHub publication failure requires bounded code and message")

        async with self._session_factory.begin() as session:
            row = await self._locked_row(session, run_id)
            current = self._decode(row)
            self._assert_intent_digest(current, intent_sha256)
            if current.state is GitHubPublicationState.PUBLISHED:
                return current

            row.state = GitHubPublicationState.FAILED.value
            row.pull_request_number = None
            row.pull_request_url = None
            row.pull_request_state = None
            row.pull_request_draft = None
            row.last_error_code = code
            row.last_error_message = message
            row.updated_at = await self._database_time(session)
            await session.flush()
            await session.refresh(row)
            return self._decode(row)

    async def _locked_row(
        self,
        session: AsyncSession,
        run_id: UUID,
    ) -> GitHubPublicationRow:
        row = (
            await session.execute(
                select(GitHubPublicationRow)
                .where(GitHubPublicationRow.run_id == run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise PersistenceConflictError(
                "GitHub publication audit row must exist before recording an external result"
            )
        return row

    @staticmethod
    async def _database_time(session: AsyncSession) -> datetime:
        value = await session.scalar(select(func.now()))
        if value is None:
            raise PersistenceCorruptionError("PostgreSQL did not return publication audit time")
        return value

    @staticmethod
    def _assert_intent_digest(
        current: PersistedGitHubPublication,
        intent_sha256: str,
    ) -> None:
        if current.intent_sha256 != intent_sha256:
            raise PersistenceConflictError(
                "GitHub publication result does not match the immutable intent hash"
            )

    @staticmethod
    def _decode(row: GitHubPublicationRow) -> PersistedGitHubPublication:
        verify_payload_hash(
            row.intent,
            row.intent_sha256,
            label=f"GitHub publication intent for Run {row.run_id}",
        )
        try:
            intent = GitHubPublicationIntent.model_validate(row.intent)
            state = GitHubPublicationState(row.state)
            return PersistedGitHubPublication(
                intent=intent,
                intent_sha256=row.intent_sha256,
                state=state,
                attempt_count=row.attempt_count,
                pull_request_number=row.pull_request_number,
                pull_request_url=row.pull_request_url,
                pull_request_state=row.pull_request_state,
                pull_request_draft=row.pull_request_draft,
                last_error_code=row.last_error_code,
                last_error_message=row.last_error_message,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        except (ValidationError, ValueError) as exc:
            raise PersistenceCorruptionError(
                f"GitHub publication audit row failed validation for Run {row.run_id}: {exc}"
            ) from exc
