from __future__ import annotations

import asyncio
import subprocess
from enum import StrEnum
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.core.settings import Settings
from app.persistence.schema import require_database_schema_current
from app.providers.errors import AgentProviderError
from app.providers.siliconflow import SiliconFlowDriver


class ReadinessState(StrEnum):
    READY = "READY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"


class ReadinessCheck(BaseModel):
    state: ReadinessState
    detail: str


class ModelReadiness(BaseModel):
    role: Literal["planner", "developer", "reviewer", "repair", "failure_explanation"]
    model: str
    state: ReadinessState


class ProductReadiness(BaseModel):
    status: Literal["READY", "NOT_READY"]
    database: ReadinessCheck
    redis: ReadinessCheck
    verification_image: ReadinessCheck
    provider: ReadinessCheck
    models: tuple[ModelReadiness, ...] = Field(default_factory=tuple)


class RuntimeDependencyHealth(BaseModel):
    """Cheap runtime transport status for the operator dashboard."""

    runtime_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    database: ReadinessCheck
    redis: ReadinessCheck
    dispatch_available: bool


class OperationalReadinessChecker:
    """Check deploy-time dependencies without becoming runtime correctness authority."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self) -> ProductReadiness:
        database, redis, verification, provider_models = await asyncio.gather(
            self._database(),
            self._redis(),
            self._verification_image(),
            self._provider_models(),
        )
        provider, models = provider_models
        required = [database.state, redis.state, verification.state, provider.state]
        required.extend(item.state for item in models)
        return ProductReadiness(
            status=(
                "READY" if all(item is ReadinessState.READY for item in required) else "NOT_READY"
            ),
            database=database,
            redis=redis,
            verification_image=verification,
            provider=provider,
            models=models,
        )

    async def _database(self) -> ReadinessCheck:
        if self._settings.database_url is None:
            return ReadinessCheck(
                state=ReadinessState.NOT_CONFIGURED,
                detail="DEVFLOW_DATABASE_URL is not configured.",
            )
        try:
            await require_database_schema_current(
                self._settings.database_url,
                echo=self._settings.database_echo,
            )
        except Exception as exc:
            return ReadinessCheck(
                state=ReadinessState.UNAVAILABLE,
                detail=f"Database/schema readiness failed: {type(exc).__name__}.",
            )
        return ReadinessCheck(state=ReadinessState.READY, detail="Database schema is current.")

    async def _redis(self) -> ReadinessCheck:
        client = Redis.from_url(
            self._settings.redis_url.get_secret_value(),
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
            decode_responses=True,
        )
        try:
            if not await client.ping():
                raise RuntimeError("Redis PING returned a false response")
        except Exception as exc:
            return ReadinessCheck(
                state=ReadinessState.UNAVAILABLE,
                detail=f"Redis readiness failed: {type(exc).__name__}.",
            )
        finally:
            await client.aclose()
        return ReadinessCheck(state=ReadinessState.READY, detail="Redis transport is reachable.")

    async def _verification_image(self) -> ReadinessCheck:
        def inspect() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    self._settings.verification_sandbox_image,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )

        try:
            result = await asyncio.to_thread(inspect)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ReadinessCheck(
                state=ReadinessState.UNAVAILABLE,
                detail=f"Verification image readiness failed: {type(exc).__name__}.",
            )
        if result.returncode != 0 or not result.stdout.strip().startswith("sha256:"):
            return ReadinessCheck(
                state=ReadinessState.UNAVAILABLE,
                detail=(
                    "Configured verification image is unavailable; build it before running tasks."
                ),
            )
        return ReadinessCheck(
            state=ReadinessState.READY,
            detail="Configured verification image is available locally.",
        )

    async def _provider_models(
        self,
    ) -> tuple[ReadinessCheck, tuple[ModelReadiness, ...]]:
        configured = (
            ("planner", self._settings.planner_model),
            ("developer", self._settings.developer_model),
            ("reviewer", self._settings.reviewer_model),
            ("repair", self._settings.repair_model),
            ("failure_explanation", self._settings.failure_explanation_model),
        )

        if self._settings.siliconflow_api_key is None:
            return (
                ReadinessCheck(
                    state=ReadinessState.NOT_CONFIGURED,
                    detail="DASHSCOPE_API_KEY (or a compatible legacy provider-key alias) is not configured.",
                ),
                tuple(
                    ModelReadiness(role=role, model=model, state=ReadinessState.NOT_CONFIGURED)
                    for role, model in configured
                ),
            )

        driver = SiliconFlowDriver.from_settings(self._settings)
        try:
            if not driver.supports_model_catalog:
                models = tuple(
                    ModelReadiness(role=role, model=model, state=ReadinessState.READY)
                    for role, model in configured
                )
                return (
                    ReadinessCheck(
                        state=ReadinessState.READY,
                        detail=(
                            "Qwen/DashScope provider is configured. The OpenAI-compatible "
                            "endpoint does not expose GET /models, so configured model ids are "
                            "treated as manually pinned and validated by real requests."
                        ),
                    ),
                    models,
                )
            available = await driver.list_model_ids()
        except AgentProviderError as exc:
            return (
                ReadinessCheck(
                    state=ReadinessState.UNAVAILABLE,
                    detail=f"Provider model catalogue failed: {exc.code.value}.",
                ),
                tuple(
                    ModelReadiness(role=role, model=model, state=ReadinessState.UNAVAILABLE)
                    for role, model in configured
                ),
            )
        finally:
            await driver.dispose()

        models = tuple(
            ModelReadiness(
                role=role,
                model=model,
                state=(
                    ReadinessState.READY if model in available else ReadinessState.MODEL_UNAVAILABLE
                ),
            )
            for role, model in configured
        )
        missing = [item for item in models if item.state is not ReadinessState.READY]
        return (
            ReadinessCheck(
                state=(ReadinessState.READY if not missing else ReadinessState.MODEL_UNAVAILABLE),
                detail=(
                    "Provider is reachable and all configured models are available."
                    if not missing
                    else (
                        "Provider is reachable but one or more configured models "
                        "are unavailable."
                    )
                ),
            ),
            models,
        )

    async def check_runtime_dependencies(
        self,
        *,
        runtime_fingerprint: str,
    ) -> RuntimeDependencyHealth:
        database, redis = await asyncio.gather(self._database(), self._redis())
        return RuntimeDependencyHealth(
            runtime_fingerprint=runtime_fingerprint,
            database=database,
            redis=redis,
            dispatch_available=(
                database.state is ReadinessState.READY and redis.state is ReadinessState.READY
            ),
        )


def attach_readiness_route(app: FastAPI, checker: OperationalReadinessChecker) -> None:
    @app.get("/readyz", response_model=ProductReadiness)
    async def readyz() -> ProductReadiness | JSONResponse:
        result = await checker.check()
        if result.status == "READY":
            return result
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"))

    @app.get("/api/v1/runtime-health", response_model=RuntimeDependencyHealth)
    async def runtime_health() -> RuntimeDependencyHealth | JSONResponse:
        fingerprint = getattr(app.state, "runtime_fingerprint", "")
        result = await checker.check_runtime_dependencies(runtime_fingerprint=fingerprint)
        if result.dispatch_available:
            return result
        return JSONResponse(status_code=503, content=result.model_dump(mode="json"))