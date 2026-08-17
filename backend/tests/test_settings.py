from pathlib import Path

from app.core.settings import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.workspace_root == Path(".devflow/workspaces")
    assert settings.siliconflow_api_key is None


def test_prefixed_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_APP_NAME", "DevFlow Test")
    monkeypatch.setenv("DEVFLOW_ENVIRONMENT", "test")
    monkeypatch.setenv("DEVFLOW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DEVFLOW_WORKSPACE_ROOT", ".devflow/test-workspaces")

    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow Test"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.workspace_root == Path(".devflow/test-workspaces")


def test_siliconflow_key_uses_unprefixed_secret_alias(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key is not None
    assert settings.siliconflow_api_key.get_secret_value() == "test-secret"
