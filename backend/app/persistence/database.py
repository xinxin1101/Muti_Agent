from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.persistence.errors import PersistenceConfigurationError

_ALLOWED_SCHEMES = ("postgresql+psycopg://", "postgresql+psycopg_async://")


def reveal_database_url(value: SecretStr | str) -> str:
    url = value.get_secret_value() if isinstance(value, SecretStr) else value
    normalized = url.strip()
    if not normalized:
        raise PersistenceConfigurationError("database URL must not be empty")
    if not normalized.startswith(_ALLOWED_SCHEMES):
        raise PersistenceConfigurationError(
            "Step 3.4 persistence requires an explicit postgresql+psycopg database URL"
        )
    return normalized


def create_postgres_engine(
    database_url: SecretStr | str,
    *,
    echo: bool = False,
) -> AsyncEngine:
    return create_async_engine(
        reveal_database_url(database_url),
        echo=echo,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False,
    )
