import asyncio
from types import SimpleNamespace

import pytest

import app.providers.siliconflow as siliconflow_module
from app.core.settings import Settings
from app.models.agent import AgentMessage, AgentRequest, AgentRole, MessageRole
from app.models.failure import FailureSource, FailureType
from app.providers import (
    AgentDriver,
    AgentProviderError,
    ProviderErrorCode,
    RoleModelConfig,
    SiliconFlowDriver,
    normalize_provider_error,
)


class FakeCompletions:
    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


class FakeStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status={status_code}")
        self.status_code = status_code


def make_request() -> AgentRequest:
    return AgentRequest(
        role=AgentRole.DEVELOPER,
        model="deepseek-ai/DeepSeek-V3.2",
        messages=[
            AgentMessage(role=MessageRole.SYSTEM, content="You are a coding agent."),
            AgentMessage(role=MessageRole.USER, content="Implement the task."),
        ],
        temperature=0.2,
        max_output_tokens=777,
    )


def test_role_model_config_is_loaded_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        planner_model="planner-model",
        developer_model="developer-model",
        reviewer_model="reviewer-model",
        repair_model="repair-model",
    )

    config = RoleModelConfig.from_settings(settings)

    assert config.for_role(AgentRole.PLANNER) == "planner-model"
    assert config.for_role(AgentRole.DEVELOPER) == "developer-model"
    assert config.for_role(AgentRole.REVIEWER) == "reviewer-model"
    assert config.for_role(AgentRole.REPAIR) == "repair-model"


def test_driver_requires_api_key_without_injected_client() -> None:
    with pytest.raises(ValueError, match="API key"):
        SiliconFlowDriver(api_key=None)


def test_driver_routes_provider_requests_through_configured_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_http_clients: list[dict] = []
    created_openai_clients: list[dict] = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            created_openai_clients.append(kwargs)

    def create_http_client(**kwargs):
        created_http_clients.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(siliconflow_module.httpx, "AsyncClient", create_http_client)
    monkeypatch.setattr(siliconflow_module, "AsyncOpenAI", FakeAsyncOpenAI)

    SiliconFlowDriver(
        api_key="test-key",
        proxy_url="http://127.0.0.1:7897/",
    )

    assert created_http_clients == [{"proxy": "http://127.0.0.1:7897"}]
    assert created_openai_clients[0]["http_client"] is not None


def test_settings_accepts_shared_clash_proxy_for_siliconflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVFLOW_PROXY_URL", "http://127.0.0.1:7897/")

    settings = Settings(_env_file=None)

    assert settings.siliconflow_proxy_url == "http://127.0.0.1:7897"


def test_driver_implements_agent_driver_protocol() -> None:
    driver = SiliconFlowDriver(client=FakeClient(FakeCompletions(response=None)))

    assert isinstance(driver, AgentDriver)


def test_complete_normalizes_response_and_usage() -> None:
    response = SimpleNamespace(
        model="deepseek-ai/DeepSeek-V3.2",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="implemented"),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=8,
            total_tokens=20,
        ),
    )
    completions = FakeCompletions(response=response)
    driver = SiliconFlowDriver(client=FakeClient(completions))

    result = asyncio.run(driver.complete(make_request()))

    assert result.model == "deepseek-ai/DeepSeek-V3.2"
    assert result.content == "implemented"
    assert result.finish_reason == "stop"
    assert result.usage.prompt_tokens == 12
    assert result.usage.completion_tokens == 8
    assert result.usage.total_tokens == 20
    assert result.latency_ms >= 0

    assert len(completions.calls) == 1
    call = completions.calls[0]
    assert call["model"] == "deepseek-ai/DeepSeek-V3.2"
    assert call["temperature"] == 0.2
    assert call["max_tokens"] == 777
    assert call["stream"] is False
    assert call["messages"] == [
        {"role": "system", "content": "You are a coding agent."},
        {"role": "user", "content": "Implement the task."},
    ]


@pytest.mark.parametrize("enable_thinking", [False, True])
def test_dashscope_requests_explicitly_control_qwen_thinking(
    enable_thinking: bool,
) -> None:
    response = SimpleNamespace(
        model="qwen3.7-flash",
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )
    completions = FakeCompletions(response=response)
    driver = SiliconFlowDriver(
        client=FakeClient(completions),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    asyncio.run(
        driver.complete(make_request().model_copy(update={"enable_thinking": enable_thinking}))
    )

    assert completions.calls[0]["extra_body"] == {"enable_thinking": enable_thinking}


def test_complete_defaults_missing_usage_to_zero() -> None:
    response = SimpleNamespace(
        model="model-a",
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason=None)],
        usage=None,
    )
    driver = SiliconFlowDriver(client=FakeClient(FakeCompletions(response=response)))

    result = asyncio.run(driver.complete(make_request()))

    assert result.usage.prompt_tokens == 0
    assert result.usage.completion_tokens == 0
    assert result.usage.total_tokens == 0


def test_empty_choices_are_rejected() -> None:
    response = SimpleNamespace(model="model-a", choices=[], usage=None)
    driver = SiliconFlowDriver(client=FakeClient(FakeCompletions(response=response)))

    with pytest.raises(AgentProviderError) as exc_info:
        asyncio.run(driver.complete(make_request()))

    assert exc_info.value.code is ProviderErrorCode.UNKNOWN
    assert exc_info.value.retryable is False


def test_timeout_is_normalized_and_convertible_to_failure_report() -> None:
    driver = SiliconFlowDriver(
        client=FakeClient(FakeCompletions(error=TimeoutError("slow provider")))
    )

    with pytest.raises(AgentProviderError) as exc_info:
        asyncio.run(driver.complete(make_request()))

    error = exc_info.value
    assert error.code is ProviderErrorCode.TIMEOUT
    assert error.retryable is True

    report = error.to_failure_report()
    assert report.failure_type is FailureType.MODEL_TIMEOUT
    assert report.source is FailureSource.PROVIDER
    assert report.retryable is True


def test_rate_limit_is_retryable() -> None:
    error = normalize_provider_error(FakeStatusError(429), provider="siliconflow")

    assert error.code is ProviderErrorCode.RATE_LIMIT
    assert error.retryable is True
    assert error.status_code == 429
    assert error.to_failure_report().failure_type is FailureType.RATE_LIMIT


def test_authentication_error_is_not_retryable() -> None:
    error = normalize_provider_error(FakeStatusError(401), provider="siliconflow")

    assert error.code is ProviderErrorCode.AUTHENTICATION
    assert error.retryable is False
    assert error.status_code == 401


def test_service_unavailable_is_retryable() -> None:
    error = normalize_provider_error(FakeStatusError(503), provider="siliconflow")

    assert error.code is ProviderErrorCode.UNAVAILABLE
    assert error.retryable is True
    assert error.status_code == 503
