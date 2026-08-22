from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.settings import Settings
from app.persistence.database import reveal_database_url
from app.persistence.errors import PersistenceConfigurationError
from app.persistence.models import PersistenceBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = PersistenceBase.metadata


def _database_url() -> str:
    settings = Settings()
    if settings.database_url is None:
        raise PersistenceConfigurationError(
            "DEVFLOW_DATABASE_URL is required. Configure the repository-root `.env`."
        )
    return reveal_database_url(settings.database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
