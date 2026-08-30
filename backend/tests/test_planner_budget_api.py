from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.autonomous import attach_autonomous_routes
from app.providers.errors import AgentProviderError, ProviderErrorCode


class _BudgetExhaustedService:
    async def create_requirement_run(self, _request):
        raise AgentProviderError(
            provider="devflow-planning-budget",
            code=ProviderErrorCode.TOKEN_BUDGET_EXHAUSTED,
            message="规划模型预算已用尽",
            retryable=False,
        )


def test_planner_budget_exhaustion_is_exposed_as_429() -> None:
    app = FastAPI()
    attach_autonomous_routes(app, _BudgetExhaustedService())  # type: ignore[arg-type]

    response = TestClient(app).post(
        "/api/v1/runs/from-requirement",
        json={"project_id": "00000000-0000-0000-0000-000000000001", "requirement": "复杂任务"},
    )

    assert response.status_code == 429
    assert response.json()["detail"].startswith(
        "本次启动的规划模型预算已用尽，未向模型服务发起超额请求。"
    )
