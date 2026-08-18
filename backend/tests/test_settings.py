from pathlib import Path

from app.core.settings import Settings


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.workspace_root == Path(".devflow/workspaces")
    assert settings.siliconflow_api_key is None
    assert settings.verification_sandbox_image == "devflow-verifier:py311"
    assert settings.verification_sandbox_cpus == 1.0
    assert settings.verification_sandbox_memory_mb == 512
    assert settings.verification_sandbox_pids_limit == 128
    assert settings.verification_sandbox_timeout_seconds == 60.0


def test_prefixed_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_APP_NAME", "DevFlow Test")
    monkeypatch.setenv("DEVFLOW_ENVIRONMENT", "test")
    monkeypatch.setenv("DEVFLOW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DEVFLOW_WORKSPACE_ROOT", ".devflow/test-workspaces")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_IMAGE", "project-verifier:test")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_CPUS", "0.5")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_MEMORY_MB", "256")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_PIDS_LIMIT", "64")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_TIMEOUT_SECONDS", "12")

    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow Test"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.workspace_root == Path(".devflow/test-workspaces")
    assert settings.verification_sandbox_image == "project-verifier:test"
    assert settings.verification_sandbox_cpus == 0.5
    assert settings.verification_sandbox_memory_mb == 256
    assert settings.verification_sandbox_pids_limit == 64
    assert settings.verification_sandbox_timeout_seconds == 12.0


def test_siliconflow_key_uses_unprefixed_secret_alias(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key is not None
    assert settings.siliconflow_api_key.get_secret_value() == "test-secret"
