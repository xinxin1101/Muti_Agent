from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.persistence.database import create_postgres_engine, create_session_factory


class PostgresFailureExplanationStore:
    """Durably cache AI prose by the immutable fingerprint of a failed Run's evidence."""

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
    ) -> PostgresFailureExplanationStore:
        return cls(engine=create_postgres_engine(database_url, echo=echo), owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def load(self, run_id: UUID, fingerprint: str) -> tuple[str, str, datetime] | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT explanation, model, created_at FROM failure_explanations "
                        "WHERE run_id = :run_id AND failure_fingerprint = :fingerprint"
                    ),
                    {"run_id": run_id, "fingerprint": fingerprint},
                )
            ).one_or_none()
        if row is None:
            return None
        return str(row.explanation), str(row.model), row.created_at

    async def save(
        self,
        *,
        run_id: UUID,
        fingerprint: str,
        model: str,
        explanation: str,
    ) -> datetime:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "INSERT INTO failure_explanations "
                    "(run_id, failure_fingerprint, model, explanation) "
                    "VALUES (:run_id, :fingerprint, :model, :explanation) "
                    "ON CONFLICT (run_id) DO UPDATE SET "
                    "failure_fingerprint = EXCLUDED.failure_fingerprint, "
                    "model = EXCLUDED.model, explanation = EXCLUDED.explanation, "
                    "created_at = now() RETURNING created_at"
                ),
                {
                    "run_id": run_id,
                    "fingerprint": fingerprint,
                    "model": model,
                    "explanation": explanation,
                },
            )
            return result.scalar_one()
