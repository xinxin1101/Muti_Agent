from __future__ import annotations

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.persistence.database import create_postgres_engine

# Keep this in lockstep with the latest migration. The startup guard must accept a database
# that already contains the current run-token-budget schema rather than treating it as stale.
EXPECTED_ALEMBIC_REVISION = "0014_flex_work_package_budgets"
_MIGRATION_HINT = "Run `cd backend && alembic upgrade head`."


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
