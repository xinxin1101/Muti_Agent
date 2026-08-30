from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.persistence.database import create_postgres_engine, create_session_factory


class ProjectCredentialConfigurationError(RuntimeError):
    """Raised before an operation would persist a secret without a local encryption key."""


class ProjectCredentialDecryptionError(RuntimeError):
    """Raised when a durable credential cannot be read with the configured local key."""


class PostgresProjectCredentialStore:
    """Persist project credentials as pgcrypto ciphertext in the Docker PostgreSQL volume."""

    def __init__(
        self,
        *,
        encryption_key: SecretStr | None,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._encryption_key = encryption_key
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._owns_engine = owns_engine

    @classmethod
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        encryption_key: SecretStr | None,
        echo: bool = False,
    ) -> PostgresProjectCredentialStore:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(encryption_key=encryption_key, engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    @property
    def enabled(self) -> bool:
        return self._encryption_key is not None

    async def save_github_publication_token(self, project_id: UUID, token: SecretStr) -> None:
        key = self._required_key()
        value = token.get_secret_value().strip()
        if not value:
            return
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO project_credentials "
                    "(project_id, github_publication_token_ciphertext, key_version, updated_at) "
                    "VALUES (:project_id, pgp_sym_encrypt(:token, :encryption_key, "
                    "'cipher-algo=aes256,compress-algo=0'), 1, now()) "
                    "ON CONFLICT (project_id) DO UPDATE SET "
                    "github_publication_token_ciphertext = "
                    "EXCLUDED.github_publication_token_ciphertext, "
                    "key_version = EXCLUDED.key_version, updated_at = now()"
                ),
                {"project_id": project_id, "token": value, "encryption_key": key},
            )

    async def load_github_publication_token(self, project_id: UUID) -> SecretStr | None:
        key = self._required_key()
        try:
            async with self._session_factory() as session:
                value = await session.scalar(
                    text(
                        "SELECT pgp_sym_decrypt("
                        "github_publication_token_ciphertext, :encryption_key) "
                        "FROM project_credentials WHERE project_id = :project_id"
                    ),
                    {"project_id": project_id, "encryption_key": key},
                )
        except DBAPIError as exc:
            raise ProjectCredentialDecryptionError(
                "The saved project publication credential cannot be decrypted. "
                "Keep DEVFLOW_SECRETS_ENCRYPTION_KEY unchanged or register the project again."
            ) from exc
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ProjectCredentialDecryptionError(
                "The saved project publication credential is invalid."
            )
        return SecretStr(value.strip())

    async def has_github_publication_token(self, project_id: UUID) -> bool:
        if not self.enabled:
            return False
        try:
            return await self.load_github_publication_token(project_id) is not None
        except ProjectCredentialDecryptionError:
            return False

    def _required_key(self) -> str:
        if self._encryption_key is None:
            raise ProjectCredentialConfigurationError(
                "DEVFLOW_SECRETS_ENCRYPTION_KEY is required for persistent project credentials"
            )
        value = self._encryption_key.get_secret_value().strip()
        if not value:
            raise ProjectCredentialConfigurationError(
                "DEVFLOW_SECRETS_ENCRYPTION_KEY must not be empty"
            )
        return value
