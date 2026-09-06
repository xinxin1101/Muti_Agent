import asyncio
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.readiness import (
    ModelReadiness,
    ProductReadiness,
    ReadinessCheck,
    ReadinessState,
    attach_readiness_route,
)
from app.core.settings import Settings
from app.providers.siliconflow import SiliconFlowDriver

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FakeModels:
    async def list(self):
        return SimpleNamespace(
            data=[
                SimpleNamespace(id="zai-org/GLM-5.2"),
                SimpleNamespace(id="Pro/deepseek-ai/DeepSeek-V3.2"),
            ]
        )


class FakeProviderClient:
    def __init__(self) -> None:
        self.models = FakeModels()


class FakeReadinessChecker:
    def __init__(self, result: ProductReadiness) -> None:
        self.result = result

    async def check(self) -> ProductReadiness:
        return self.result


def _check(state: ReadinessState) -> ReadinessCheck:
    return ReadinessCheck(state=state, detail=state.value)


def test_current_agent_models_are_configuration_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.planner_model == "qwen3.7-flash"
    assert settings.developer_model == "qwen3.7-flash"
    assert settings.reviewer_model == "qwen3.7-flash"
    assert settings.repair_model == "qwen3.7-flash"
    assert settings.failure_explanation_model == "qwen3.7-flash"


def test_publication_and_read_credentials_are_distinct() -> None:
    settings = Settings(
        _env_file=None,
        github_read_token=SecretStr("read-only"),
        github_publication_token=SecretStr("publish-only"),
    )

    assert settings.github_read_token is not None
    assert settings.github_read_token.get_secret_value() == "read-only"
    assert settings.effective_github_publication_token is not None
    assert settings.effective_github_publication_token.get_secret_value() == "publish-only"


def test_legacy_github_token_is_publication_only_fallback() -> None:
    settings = Settings(_env_file=None, github_token=SecretStr("legacy"))

    assert settings.github_read_token is None
    assert settings.effective_github_publication_token is not None
    assert settings.effective_github_publication_token.get_secret_value() == "legacy"


def test_siliconflow_model_catalogue_is_typed_and_bounded() -> None:
    driver = SiliconFlowDriver(client=FakeProviderClient())

    result = asyncio.run(driver.list_model_ids())

    assert result == frozenset({"zai-org/GLM-5.2", "Pro/deepseek-ai/DeepSeek-V3.2"})


def test_readyz_returns_503_when_operational_dependencies_are_not_ready() -> None:
    result = ProductReadiness(
        status="NOT_READY",
        database=_check(ReadinessState.READY),
        redis=_check(ReadinessState.READY),
        verification_image=_check(ReadinessState.READY),
        provider=_check(ReadinessState.MODEL_UNAVAILABLE),
        models=(
            ModelReadiness(
                role="planner",
                model="retired-model",
                state=ReadinessState.MODEL_UNAVAILABLE,
            ),
        ),
    )
    app = FastAPI()
    attach_readiness_route(app, FakeReadinessChecker(result))  # type: ignore[arg-type]

    response = TestClient(app).get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "NOT_READY"
    assert response.json()["models"][0]["state"] == "MODEL_UNAVAILABLE"


def test_readyz_returns_200_only_for_full_operational_readiness() -> None:
    result = ProductReadiness(
        status="READY",
        database=_check(ReadinessState.READY),
        redis=_check(ReadinessState.READY),
        verification_image=_check(ReadinessState.READY),
        provider=_check(ReadinessState.READY),
        models=(
            ModelReadiness(
                role="planner",
                model="zai-org/GLM-5.2",
                state=ReadinessState.READY,
            ),
        ),
    )
    app = FastAPI()
    attach_readiness_route(app, FakeReadinessChecker(result))  # type: ignore[arg-type]

    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "READY"


def test_public_env_example_contains_all_runtime_model_and_credential_keys() -> None:
    template = (_REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8")

    for key in (
        "DEVFLOW_PLANNER_MODEL",
        "DEVFLOW_DEVELOPER_MODEL",
        "DEVFLOW_REVIEWER_MODEL",
        "DEVFLOW_REPAIR_MODEL",
        "DEVFLOW_GITHUB_READ_TOKEN",
        "DEVFLOW_GITHUB_PUBLICATION_TOKEN",
        "DEVFLOW_GIT_CLONE_TIMEOUT_SECONDS",
    ):
        assert f"{key}=" in template