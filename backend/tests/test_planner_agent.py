import asyncio
import json

import pytest

from app import agents, models


VALID_TASK = {
    "task_id": "AUTH-001",
    "objective": "Add JWT login support to the FastAPI application.",
    "readable_files": ["app/**", "tests/**"],
    "writable_files": ["app/auth/**"],
    "readonly_files": ["tests/**"],
    "acceptance_criteria": [
        "Valid credentials return a JWT access token.",
        "Invalid credentials are rejected without issuing a token.",
    ],
    "verification_commands": ["pytest -q", "ruff check ."],
    "max_retries": 2,
}


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse | Exception]) -> None:
        self._responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeDriver received more calls than expected")

        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(content: str) -> models.AgentResponse:
    return models.AgentResponse(
        model="test/planner",
        content=content,
        latency_ms=5,
        finish_reason="stop",
    )


def test_planner_returns_valid_task_contract_without_repair() -> None:
    driver = FakeDriver([_response(json.dumps(VALID_TASK))])
    planner = agents.PlannerAgent(driver=driver, model="test/planner")

    task = asyncio.run(planner.plan("Add JWT login support to the FastAPI application."))

    assert task.task_id == "AUTH-001"
    assert task.writable_files == ["app/auth/**"]
    assert len(driver.requests) == 1
    assert driver.requests[0].role is models.AgentRole.PLANNER
    assert driver.requests[0].model == "test/planner"
    assert driver.requests[0].messages[0].role.value == "system"
    assert "TaskContract JSON Schema" in driver.requests[0].messages[0].content


def test_planner_repairs_invalid_output_once() -> None:
    invalid_output = json.dumps({"task_id": "AUTH-001"})
    driver = FakeDriver(
        [
            _response(invalid_output),
            _response(json.dumps(VALID_TASK)),
        ]
    )
    planner = agents.PlannerAgent(
        driver=driver,
        model="test/planner",
        max_schema_repair_attempts=1,
    )

    task = asyncio.run(planner.plan("Add JWT login support."))

    assert task.task_id == "AUTH-001"
    assert len(driver.requests) == 2
    repair_request = driver.requests[1]
    assert repair_request.temperature == 0.0
    assert "Pydantic validation error" in repair_request.messages[1].content
    assert invalid_output in repair_request.messages[1].content


def test_planner_rejects_output_after_schema_repair_budget_is_exhausted() -> None:
    driver = FakeDriver(
        [
            _response("not-json"),
            _response(json.dumps({"task_id": "still-invalid"})),
        ]
    )
    planner = agents.PlannerAgent(
        driver=driver,
        model="test/planner",
        max_schema_repair_attempts=1,
    )

    with pytest.raises(agents.InvalidPlannerOutputError) as exc_info:
        asyncio.run(planner.plan("Add JWT login support."))

    failure = exc_info.value.failure
    assert failure.failure_type is models.FailureType.INVALID_AGENT_OUTPUT
    assert failure.source is models.FailureSource.RUNTIME
    assert failure.retryable is False
    assert "schema-repair" in failure.message
    assert len(driver.requests) == 2


def test_planner_does_not_repair_when_budget_is_zero() -> None:
    driver = FakeDriver([_response("```json\n{}\n```")])
    planner = agents.PlannerAgent(
        driver=driver,
        model="test/planner",
        max_schema_repair_attempts=0,
    )

    with pytest.raises(agents.InvalidPlannerOutputError):
        asyncio.run(planner.plan("Create one implementation task."))

    assert len(driver.requests) == 1


def test_planner_rejects_empty_requirement_before_calling_driver() -> None:
    driver = FakeDriver([])
    planner = agents.PlannerAgent(driver=driver, model="test/planner")

    with pytest.raises(ValueError, match="requirement must not be empty"):
        asyncio.run(planner.plan("   "))

    assert driver.requests == []


def test_planner_includes_caller_supplied_repository_context_without_fetching_it() -> None:
    driver = FakeDriver([_response(json.dumps(VALID_TASK))])
    planner = agents.PlannerAgent(driver=driver, model="test/planner")

    asyncio.run(
        planner.plan(
            "Add JWT login support.",
            repository_context="FastAPI entrypoint is app/main.py; tests live under tests/.",
        )
    )

    user_message = driver.requests[0].messages[1].content
    assert "app/main.py" in user_message
    assert "tests/" in user_message


def test_planner_propagates_provider_failure_without_treating_it_as_schema_error() -> None:
    provider_failure = RuntimeError("provider unavailable")
    driver = FakeDriver([provider_failure])
    planner = agents.PlannerAgent(driver=driver, model="test/planner")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(planner.plan("Add JWT login support."))

    assert len(driver.requests) == 1


@pytest.mark.parametrize(
    ("model", "repair_attempts", "temperature", "message"),
    [
        ("   ", 1, 0.1, "planner model must not be empty"),
        ("test/planner", -1, 0.1, "max_schema_repair_attempts"),
        ("test/planner", 4, 0.1, "max_schema_repair_attempts"),
        ("test/planner", 1, -0.1, "temperature"),
        ("test/planner", 1, 2.1, "temperature"),
    ],
)
def test_planner_configuration_is_bounded(
    model: str,
    repair_attempts: int,
    temperature: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        agents.PlannerAgent(
            driver=FakeDriver([]),
            model=model,
            max_schema_repair_attempts=repair_attempts,
            temperature=temperature,
        )
