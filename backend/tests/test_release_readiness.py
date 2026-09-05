from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.readiness import OperationalReadinessChecker, ReadinessState
from app.core import settings as settings_module
from app.core.settings import Settings
from app.persistence.schema import (
    EXPECTED_ALEMBIC_REVISION,
    DatabaseSchemaNotReadyError,
    require_database_schema_current,
)


def test_database_schema_preflight_uses_checked_in_alembic_head() -> None:
    assert EXPECTED_ALEMBIC_REVISION == "0024_liveness_credit"


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


def test_repository_env_and_workspace_are_independent_of_current_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    expected_env = repository_root / ".env"
    expected_workspace = (repository_root / ".devflow/workspaces").resolve()

    assert expected_env == settings_module._REPOSITORY_ENV_FILE
    assert Settings.model_config["env_file"] == expected_env

    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=None)

    assert expected_env == settings_module._REPOSITORY_ENV_FILE
    assert Settings.model_config["env_file"] == expected_env
    assert settings.workspace_root == expected_workspace


def test_blank_optional_provider_secrets_are_not_treated_as_configured() -> None:
    settings = Settings(
        _env_file=None,
        SILICONFLOW_API_KEY="",
        github_token="   ",
    )

    assert settings.siliconflow_api_key is None
    assert settings.github_token is None


def test_dashscope_readiness_uses_manually_pinned_models_without_catalog_call() -> None:
    settings = Settings(
        _env_file=None,
        DASHSCOPE_API_KEY="test-secret",
        siliconflow_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        planner_model="qwen3.7-flash",
        developer_model="qwen3.7-flash",
        reviewer_model="qwen3.7-flash",
        repair_model="qwen3.7-flash",
        failure_explanation_model="qwen3.7-flash",
    )
    checker = OperationalReadinessChecker(settings)

    provider, models = asyncio.run(checker._provider_models())

    assert provider.state is ReadinessState.READY
    assert "does not expose GET /models" in provider.detail
    assert models
    assert all(item.state is ReadinessState.READY for item in models)
    assert {item.model for item in models} == {"qwen3.7-flash"}


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