from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.persistence.database import create_postgres_engine

_MIGRATION_HINT = "Run `cd backend && alembic upgrade head`."


@lru_cache(maxsize=1)
def expected_alembic_revision() -> str:
    """Resolve the checked-in Alembic head instead of duplicating it in application code."""

    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("DevFlow Alembic migration head is not configured")
    return head


# Kept as a module value for existing callers/tests; its source of truth is Alembic's script tree.
EXPECTED_ALEMBIC_REVISION = expected_alembic_revision()


class DatabaseSchemaNotReadyError(RuntimeError):
    """Raised when the configured PostgreSQL schema is not at the accepted Alembic head."""


async def require_database_schema_current(
    database_url: SecretStr | str,
    *,
    echo: bool = False,
) -> None:
    """Fail fast unless PostgreSQL reports the single accepted Alembic head revision."""

    engine = create_postgres_engine(database_url, echo=echo)
    try:
        try:
            async with engine.connect() as connection:
                result = await connection.execute(text("SELECT version_num FROM alembic_version"))
                revisions = tuple(str(value) for value in result.scalars().all())
        except SQLAlchemyError as exc:
            raise DatabaseSchemaNotReadyError(
                f"DevFlow database schema is not ready. {_MIGRATION_HINT}"
            ) from exc
    finally:
        await engine.dispose()

    if revisions != (EXPECTED_ALEMBIC_REVISION,):
        current = ", ".join(revisions) if revisions else "<none>"
        raise DatabaseSchemaNotReadyError(
            "DevFlow database schema revision mismatch: "
            f"expected {EXPECTED_ALEMBIC_REVISION}, found {current}. {_MIGRATION_HINT}"
        )
