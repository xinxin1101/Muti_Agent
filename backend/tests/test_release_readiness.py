from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import settings as settings_module
from app.core.settings import Settings
from app.persistence.schema import (
    EXPECTED_ALEMBIC_REVISION,
    DatabaseSchemaNotReadyError,
    require_database_schema_current,
)


class _FakeScalarResult:
    def __init__(self, revisions: tuple[str, ...]) -> None:
        self._revisions = revisions

    def all(self) -> tuple[str, ...]:
        return self._revisions


class _FakeResult:
    def __init__(self, revisions: tuple[str, ...]) -> None:
        self._revisions = revisions

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._revisions)


class _FakeConnection:
    def __init__(self, revisions: tuple[str, ...]) -> None:
        self._revisions = revisions

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def execute(self, _statement) -> _FakeResult:
        return _FakeResult(self._revisions)


class _FakeEngine:
    def __init__(self, revisions: tuple[str, ...]) -> None:
        self._revisions = revisions
        self.disposed = False

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._revisions)

    async def dispose(self) -> None:
        self.disposed = True


def test_repository_env_file_is_independent_of_current_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    expected = Path(__file__).resolve().parents[2] / ".env"
    assert settings_module._REPOSITORY_ENV_FILE == expected
    assert Settings.model_config["env_file"] == expected

    monkeypatch.chdir(tmp_path)

    assert settings_module._REPOSITORY_ENV_FILE == expected
    assert Settings.model_config["env_file"] == expected


def test_blank_optional_provider_secrets_are_not_treated_as_configured() -> None:
    settings = Settings(
        _env_file=None,
        SILICONFLOW_API_KEY="",
        github_token="   ",
    )

    assert settings.siliconflow_api_key is None
    assert settings.github_token is None


def test_database_schema_preflight_accepts_expected_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine((EXPECTED_ALEMBIC_REVISION,))
    monkeypatch.setattr(
        "app.persistence.schema.create_postgres_engine",
        lambda *_args, **_kwargs: engine,
    )

    asyncio.run(require_database_schema_current("postgresql+psycopg://ignored"))

    assert engine.disposed is True


def test_database_schema_preflight_rejects_stale_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _FakeEngine(("0006_github_publications",))
    monkeypatch.setattr(
        "app.persistence.schema.create_postgres_engine",
        lambda *_args, **_kwargs: engine,
    )

    with pytest.raises(DatabaseSchemaNotReadyError, match="alembic upgrade head"):
        asyncio.run(require_database_schema_current("postgresql+psycopg://ignored"))

    assert engine.disposed is True
