from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.settings import Settings

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_default_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.workspace_root == (_REPOSITORY_ROOT / ".devflow/workspaces").resolve()
    assert settings.worker_id is None
    assert settings.worker_lease_seconds == 60.0
    assert settings.worker_heartbeat_interval_seconds == 15.0
    assert settings.worker_task_time_limit_seconds == 7_200.0
    assert settings.worker_terminal_persistence_grace_seconds == 180.0
    assert settings.continuation_max_slices == 5
    assert settings.continuation_total_budget_seconds == 3_600.0
    assert settings.continuation_max_repeated_file_slices == 1
    assert settings.dag_max_concurrent_tasks == 2
    assert settings.developer_max_duration_seconds == 600.0
    assert settings.developer_max_model_turn_seconds == 180.0
    assert settings.repair_max_duration_seconds == 300.0
    assert settings.repair_max_model_turn_seconds == 90.0
    assert settings.minimum_repair_attempts == 2
    assert settings.planner_max_output_tokens == 1_200
    assert settings.developer_max_output_tokens == 1_400
    assert settings.reviewer_max_output_tokens == 800
    assert settings.repair_max_output_tokens == 1_000
    assert settings.failure_explanation_max_output_tokens == 400
    assert settings.planner_enable_thinking is False
    assert settings.developer_enable_thinking is False
    assert settings.reviewer_enable_thinking is False
    assert settings.repair_enable_thinking is False
    assert settings.failure_explanation_enable_thinking is False
    assert settings.planner_token_budget_tokens == 4_000
    assert settings.planner_max_attempts == 2
    assert settings.siliconflow_api_key is None
    assert settings.verification_sandbox_image == "devflow-verifier:py311"
    assert settings.verification_sandbox_cpus == 1.0
    assert settings.verification_sandbox_memory_mb == 512
    assert settings.verification_sandbox_pids_limit == 128
    assert settings.verification_sandbox_timeout_seconds == 60.0
    assert settings.dependency_proxy_url is None
    assert settings.dependency_python_index_url == "https://pypi.org/simple/"
    assert settings.dependency_node_registry_url == "https://registry.npmjs.org/"
    assert settings.dependency_preflight_timeout_seconds == 10.0
    assert settings.dependency_preflight_build_timeout_seconds == 60.0


def test_prefixed_environment_variables_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_APP_NAME", "DevFlow Test")
    monkeypatch.setenv("DEVFLOW_ENVIRONMENT", "test")
    monkeypatch.setenv("DEVFLOW_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DEVFLOW_WORKSPACE_ROOT", ".devflow/test-workspaces")
    monkeypatch.setenv("DEVFLOW_WORKER_ID", "worker-test-01")
    monkeypatch.setenv("DEVFLOW_WORKER_LEASE_SECONDS", "30")
    monkeypatch.setenv("DEVFLOW_WORKER_HEARTBEAT_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("DEVFLOW_WORKER_TASK_TIME_LIMIT_SECONDS", "7200")
    monkeypatch.setenv("DEVFLOW_REPAIR_MAX_DURATION_SECONDS", "240")
    monkeypatch.setenv("DEVFLOW_REPAIR_MAX_MODEL_TURN_SECONDS", "80")
    monkeypatch.setenv("DEVFLOW_MINIMUM_REPAIR_ATTEMPTS", "2")
    monkeypatch.setenv("DEVFLOW_PLANNER_MAX_OUTPUT_TOKENS", "700")
    monkeypatch.setenv("DEVFLOW_DEVELOPER_MAX_OUTPUT_TOKENS", "1700")
    monkeypatch.setenv("DEVFLOW_REVIEWER_MAX_OUTPUT_TOKENS", "600")
    monkeypatch.setenv("DEVFLOW_REPAIR_MAX_OUTPUT_TOKENS", "900")
    monkeypatch.setenv("DEVFLOW_FAILURE_EXPLANATION_MAX_OUTPUT_TOKENS", "300")
    monkeypatch.setenv("DEVFLOW_PLANNER_ENABLE_THINKING", "true")
    monkeypatch.setenv("DEVFLOW_DEVELOPER_ENABLE_THINKING", "true")
    monkeypatch.setenv("DEVFLOW_REVIEWER_ENABLE_THINKING", "true")
    monkeypatch.setenv("DEVFLOW_REPAIR_ENABLE_THINKING", "true")
    monkeypatch.setenv("DEVFLOW_FAILURE_EXPLANATION_ENABLE_THINKING", "true")
    monkeypatch.setenv("DEVFLOW_PLANNER_TOKEN_BUDGET_TOKENS", "3000")
    monkeypatch.setenv("DEVFLOW_PLANNER_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_IMAGE", "project-verifier:test")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_CPUS", "0.5")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_MEMORY_MB", "256")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_PIDS_LIMIT", "64")
    monkeypatch.setenv("DEVFLOW_VERIFICATION_SANDBOX_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("DEVFLOW_PROXY_URL", "http://127.0.0.1:7897/")
    monkeypatch.setenv("DEVFLOW_DEPENDENCY_PYTHON_INDEX_URL", "https://pypi.example/simple")
    monkeypatch.setenv("DEVFLOW_DEPENDENCY_NODE_REGISTRY_URL", "https://npm.example")
    monkeypatch.setenv("DEVFLOW_DEPENDENCY_PREFLIGHT_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("DEVFLOW_DEPENDENCY_PREFLIGHT_BUILD_TIMEOUT_SECONDS", "45")

    settings = Settings(_env_file=None)

    assert settings.app_name == "DevFlow Test"
    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.workspace_root == (_REPOSITORY_ROOT / ".devflow/test-workspaces").resolve()
    assert settings.worker_id == "worker-test-01"
    assert settings.worker_lease_seconds == 30.0
    assert settings.worker_heartbeat_interval_seconds == 5.0
    assert settings.worker_task_time_limit_seconds == 7_200.0
    assert settings.repair_max_duration_seconds == 240.0
    assert settings.repair_max_model_turn_seconds == 80.0
    assert settings.minimum_repair_attempts == 2
    assert settings.planner_max_output_tokens == 700
    assert settings.developer_max_output_tokens == 1700
    assert settings.reviewer_max_output_tokens == 600
    assert settings.repair_max_output_tokens == 900
    assert settings.failure_explanation_max_output_tokens == 300
    assert settings.planner_enable_thinking is True
    assert settings.developer_enable_thinking is True
    assert settings.reviewer_enable_thinking is True
    assert settings.repair_enable_thinking is True
    assert settings.failure_explanation_enable_thinking is True
    assert settings.planner_token_budget_tokens == 3_000
    assert settings.planner_max_attempts == 1
    assert settings.verification_sandbox_image == "project-verifier:test"
    assert settings.verification_sandbox_cpus == 0.5
    assert settings.verification_sandbox_memory_mb == 256
    assert settings.verification_sandbox_pids_limit == 64
    assert settings.verification_sandbox_timeout_seconds == 12.0
    assert settings.dependency_proxy_url == "http://127.0.0.1:7897"
    assert settings.dependency_python_index_url == "https://pypi.example/simple/"
    assert settings.dependency_node_registry_url == "https://npm.example/"
    assert settings.dependency_preflight_timeout_seconds == 8.0
    assert settings.dependency_preflight_build_timeout_seconds == 45.0


def test_worker_heartbeat_interval_must_be_shorter_than_lease(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_WORKER_LEASE_SECONDS", "10")
    monkeypatch.setenv("DEVFLOW_WORKER_HEARTBEAT_INTERVAL_SECONDS", "10")

    with pytest.raises(ValidationError, match="shorter than the lease"):
        Settings(_env_file=None)


def test_worker_task_time_limit_must_cover_the_entire_bounded_lifecycle(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_DEVELOPER_MAX_DURATION_SECONDS", "600")
    monkeypatch.setenv("DEVFLOW_WORKER_TASK_TIME_LIMIT_SECONDS", "2")

    with pytest.raises(ValidationError, match="maximum bounded task lifecycle"):
        Settings(_env_file=None)


def test_agent_model_turn_must_fit_within_its_total_budget(monkeypatch) -> None:
    monkeypatch.setenv("DEVFLOW_REPAIR_MAX_DURATION_SECONDS", "60")
    monkeypatch.setenv("DEVFLOW_REPAIR_MAX_MODEL_TURN_SECONDS", "61")

    with pytest.raises(ValidationError, match="repair model turn limit"):
        Settings(_env_file=None)


def test_siliconflow_key_uses_unprefixed_secret_alias(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_API_KEY", "test-secret")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key is not None
    assert settings.siliconflow_api_key.get_secret_value() == "test-secret"


def test_gemini_key_uses_provider_compatible_secret_alias(monkeypatch) -> None:
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-secret")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key is not None
    assert settings.siliconflow_api_key.get_secret_value() == "gemini-test-secret"


def test_dashscope_key_uses_provider_compatible_secret_alias(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-test-secret")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_api_key is not None
    assert settings.siliconflow_api_key.get_secret_value() == "dashscope-test-secret"
