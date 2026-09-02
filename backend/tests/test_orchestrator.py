import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from app import agents, models
from app.models.run import TaskRunState
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.runtime.state_machine import TaskStateMachine
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeDriver received an unexpected model call")
        return self.responses.pop(0)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
    prompt_tokens: int = 4,
    completion_tokens: int = 2,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        latency_ms=3,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    tests = root / "tests"
    tests.mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tests / "test_value.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_value():\n"
        "    text = Path('module.py').read_text(encoding='utf-8')\n"
        "    assert 'VALUE = 2' in text\n",
        encoding="utf-8",
    )
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task(*, max_retries: int = 2) -> models.TaskContract:
    return models.TaskContract(
        task_id="RUN-001",
        objective="Change VALUE to 2 while keeping tests protected.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["module.py contains VALUE = 2"],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=max_retries,
    )


def _patch(call_id: str, old: str, new: str) -> models.ToolCall:
    return models.ToolCall(
        id=call_id,
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": old,
                "new_text": new,
            }
        ),
    )


def _review_pass() -> str:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.PASS,
        summary="The implementation satisfies the task and verified diff.",
        issues=[],
    ).model_dump_json()


def _review_changes_requested() -> str:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.CHANGES_REQUESTED,
        summary="One semantic change is still required.",
        issues=[
            models.ReviewIssue(
                severity=models.ReviewSeverity.MEDIUM,
                message="Add the required reviewed marker without changing protected tests.",
                file="module.py",
                line=1,
            )
        ],
    ).model_dump_json()


class TimeoutThenRepair:
    """Simulate a timeout after inspection without producing a candidate patch."""

    def __init__(self) -> None:
        self.attempts: list[int] = []
        self.failure_evidence: list[list[str]] = []

    async def repair(self, task, failures, *, attempt, workspace, context_packet, trace=None):
        del task, context_packet, trace
        self.attempts.append(attempt)
        self.failure_evidence.append(list(failures[0].evidence))
        if attempt == 1:
            return models.RepairRunResult(
                attempt=attempt,
                failure_types=[models.FailureType.TEST_FAILURE],
                stop_reason=models.RepairStopReason.TIME_LIMIT,
                iterations=1,
                tool_calls=1,
                changed_files=workspace.changed_files(),
                usage=models.TokenUsage(),
                latency_ms=1,
            )
        (workspace.root / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        return models.RepairRunResult(
            attempt=attempt,
            failure_types=[models.FailureType.TEST_FAILURE],
            stop_reason=models.RepairStopReason.MODEL_STOP,
            iterations=1,
            tool_calls=1,
            changed_files=workspace.changed_files(),
            usage=models.TokenUsage(),
            latency_ms=1,
        )


class AlwaysNoPatchRepair:
    """Simulate a bounded Repair implementation that never mutates the workspace."""

    def __init__(self) -> None:
        self.attempts: list[int] = []

    async def repair(self, task, failures, *, attempt, workspace, context_packet, trace=None):
        del task, failures, context_packet, trace
        self.attempts.append(attempt)
        return models.RepairRunResult(
            attempt=attempt,
            failure_types=[models.FailureType.TEST_FAILURE],
            stop_reason=models.RepairStopReason.NO_PROGRESS,
            iterations=2,
            tool_calls=0,
            final_message="No candidate patch was produced.",
            changed_files=workspace.changed_files(),
            usage=models.TokenUsage(),
            latency_ms=1,
        )


class WritesPatchRepair:
    """Simulate a Repair Agent that produces one controlled candidate patch."""

    def __init__(self, *, value: int) -> None:
        self._value = value
        self.attempts: list[int] = []

    async def repair(self, task, failures, *, attempt, workspace, context_packet, trace=None):
        del task, failures, context_packet, trace
        self.attempts.append(attempt)
        (workspace.root / "module.py").write_text(f"VALUE = {self._value}\n", encoding="utf-8")
        return models.RepairRunResult(
            attempt=attempt,
            failure_types=[models.FailureType.TEST_FAILURE],
            stop_reason=models.RepairStopReason.MODEL_STOP,
            iterations=1,
            tool_calls=1,
            changed_files=workspace.changed_files(),
            usage=models.TokenUsage(),
            latency_ms=1,
        )


class SequenceVerifier:
    def __init__(self, passed: list[bool], *, failure_summaries: list[str] | None = None) -> None:
        self._passed = iter(passed)
        self._failure_summaries = iter(failure_summaries or [])
        self.calls = 0

    def verify(self, task, *, workspace):
        del task, workspace
        self.calls += 1
        passed = next(self._passed)
        summary = ""
        if not passed:
            summary = next(self._failure_summaries, "unchanged failure")
        return models.VerificationResult(
            passed=passed,
            checks=[
                models.CheckResult(
                    check_type=models.CheckType.TEST,
                    name="test",
                    command="pytest -q",
                    passed=passed,
                    exit_code=0 if passed else 1,
                    stderr=summary,
                    failure_type=None if passed else models.FailureType.TEST_FAILURE,
                )
            ],
        )


def _orchestrator(
    *,
    developer_driver: FakeDriver,
    repair_driver: FakeDriver,
    reviewer_driver: FakeDriver,
) -> SingleTaskOrchestrator:
    return SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=repair_driver, model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )


def test_complete_loop_repairs_first_test_failure_then_succeeds(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 3", "VALUE = 2")]),
            _response(content="Fixed the failing assertion target."),
        ]
    )
    reviewer_driver = FakeDriver([_response(content=_review_pass())])

    result = asyncio.run(
        _orchestrator(
            developer_driver=developer_driver,
            repair_driver=repair_driver,
            reviewer_driver=reviewer_driver,
        ).run(_task(), workspace=workspace)
    )

    assert result.status is TaskRunState.SUCCEEDED
    assert result.changed_files == ["module.py"]
    assert result.repair_attempts == 1
    assert result.repairs[0].progress is not None
    assert result.repairs[0].progress.status is models.RepairProgressStatus.REPAIRED
    assert result.repairs[0].progress.has_patch is True
    assert result.repairs[0].progress.files_changed == ["module.py"]
    assert result.repairs[0].progress.validation_executed is True
    assert len(result.verifications) == 2
    assert result.verifications[0].passed is False
    assert result.verifications[1].passed is True
    assert result.reviews[-1].decision is models.ReviewOutcome.PASS
    assert result.failures == []
    assert [event.state for event in result.events] == [
        TaskRunState.PENDING,
        TaskRunState.RUNNING,
        TaskRunState.VERIFYING,
        TaskRunState.REPAIRING,
        TaskRunState.VERIFYING,
        TaskRunState.REVIEWING,
        TaskRunState.SUCCEEDED,
    ]
    assert result.agent_models[models.AgentRole.DEVELOPER] == "fake/developer"
    assert {item.role for item in result.agent_usage} == {
        models.AgentRole.DEVELOPER,
        models.AgentRole.REPAIR,
    }
    assert "VALUE = 2" in (root / "module.py").read_text(encoding="utf-8")
    assert len(developer_driver.requests) == 2
    assert len(repair_driver.requests) == 2
    assert len(reviewer_driver.requests) == 1


def test_semantic_review_rejection_repairs_then_reverifies_and_passes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 2")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 2", "VALUE = 2  # reviewed")]),
            _response(content="Applied the semantic review fix."),
        ]
    )
    reviewer_driver = FakeDriver(
        [
            _response(content=_review_changes_requested()),
            _response(content=_review_pass()),
        ]
    )

    result = asyncio.run(
        _orchestrator(
            developer_driver=developer_driver,
            repair_driver=repair_driver,
            reviewer_driver=reviewer_driver,
        ).run(_task(), workspace=workspace)
    )

    assert result.status is TaskRunState.SUCCEEDED
    assert len(result.verifications) == 2
    assert all(verification.passed for verification in result.verifications)
    assert [review.decision for review in result.reviews] == [
        models.ReviewOutcome.CHANGES_REQUESTED,
        models.ReviewOutcome.PASS,
    ]
    assert result.repairs[0].failure_types == [models.FailureType.REVIEW_REJECTED]
    assert [event.state for event in result.events] == [
        TaskRunState.PENDING,
        TaskRunState.RUNNING,
        TaskRunState.VERIFYING,
        TaskRunState.REVIEWING,
        TaskRunState.REPAIRING,
        TaskRunState.VERIFYING,
        TaskRunState.REVIEWING,
        TaskRunState.SUCCEEDED,
    ]
    assert any(
        "REVIEW_REJECTED" in message.content for message in repair_driver.requests[0].messages
    )


def test_retry_budget_exhaustion_preserves_test_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 3", "VALUE = 4")]),
            _response(content="Repair attempt completed."),
        ]
    )
    reviewer_driver = FakeDriver([])

    result = asyncio.run(
        _orchestrator(
            developer_driver=developer_driver,
            repair_driver=repair_driver,
            reviewer_driver=reviewer_driver,
        ).run(_task(max_retries=1), workspace=workspace)
    )

    assert result.status is TaskRunState.FAILED
    assert result.repair_attempts == 1
    assert len(result.verifications) == 2
    assert result.failures[0].failure_type is models.FailureType.TEST_FAILURE
    assert result.failures[0].retryable is False
    assert "Repair retry budget was exhausted" in result.failures[0].message
    assert any(item == "repair_attempts_exhausted=1" for item in result.failures[0].evidence)
    assert reviewer_driver.requests == []


def test_developer_time_limit_is_not_misclassified_as_a_tool_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("read-only", "VALUE = 1", "VALUE = 1")]),
        ]
    )
    times = iter([0.0, 0.0, 2.0])
    developer = agents.DeveloperAgent(
        driver=developer_driver,
        model="fake/developer",
        max_duration_seconds=1.0,
        max_model_turn_seconds=1.0,
        clock=lambda: next(times),
    )
    orchestrator = SingleTaskOrchestrator(
        developer=developer,
        verifier=DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=FakeDriver([]), model="fake/reviewer"),
        repair=agents.RepairAgent(driver=FakeDriver([]), model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )

    result = asyncio.run(orchestrator.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.FAILED
    assert result.developer is not None
    assert result.developer.stop_reason is models.DeveloperStopReason.TIME_LIMIT
    assert result.developer.execution_budget is not None
    assert result.failures[0].failure_type is models.FailureType.AGENT_TIME_LIMIT
    assert result.failures[0].message == "开发智能体时间预算耗尽，未能在限制内完成代码修改。"
    assert "developer_max_duration_seconds=1" in result.failures[0].evidence
    assert "developer_max_model_turn_seconds=1" in result.failures[0].evidence
    assert "developer_model_latency_ms=3" in result.failures[0].evidence


def test_bounded_developer_slice_with_changes_verifies_without_an_extra_model_turn(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [_response(tool_calls=[_patch("change", "VALUE = 1", "VALUE = 2")])]
    )
    times = iter([0.0, 0.0, 2.0])
    developer = agents.DeveloperAgent(
        driver=developer_driver,
        model="fake/developer",
        max_duration_seconds=1.0,
        max_model_turn_seconds=1.0,
        clock=lambda: next(times),
    )
    reviewer_driver = FakeDriver([_response(content=_review_pass())])
    orchestrator = SingleTaskOrchestrator(
        developer=developer,
        verifier=SequenceVerifier([True]),  # type: ignore[arg-type]
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=FakeDriver([]), model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )

    result = asyncio.run(orchestrator.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert result.developer is not None
    assert result.developer.stop_reason is models.DeveloperStopReason.TIME_LIMIT
    assert len(developer_driver.requests) == 1
    assert len(result.verifications) == 1


def test_first_no_patch_repair_uses_minimum_second_attempt_before_verification(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair = TimeoutThenRepair()
    reviewer_driver = FakeDriver([_response(content=_review_pass())])
    verifier = SequenceVerifier([False, True])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=verifier,  # type: ignore[arg-type]
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=repair,  # type: ignore[arg-type]
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
        minimum_repair_attempts=2,
    )

    result = asyncio.run(orchestrator.run(_task(max_retries=1), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert repair.attempts == [1, 2]
    assert result.repair_attempts == 2
    assert result.repairs[0].progress is not None
    assert result.repairs[0].progress.status is models.RepairProgressStatus.NO_PATCH_PRODUCED
    assert result.repairs[0].progress.has_patch is False
    assert result.repairs[0].progress.validation_executed is False
    assert result.repairs[1].progress is not None
    assert result.repairs[1].progress.has_patch is True
    assert verifier.calls == 2
    assert len(result.verifications) == 2


def test_second_no_patch_repair_becomes_terminal_without_reverification(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair = AlwaysNoPatchRepair()
    verifier = SequenceVerifier([False])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=verifier,  # type: ignore[arg-type]
        reviewer=agents.ReviewerAgent(driver=FakeDriver([]), model="fake/reviewer"),
        repair=repair,  # type: ignore[arg-type]
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
        minimum_repair_attempts=2,
    )

    result = asyncio.run(orchestrator.run(_task(max_retries=1), workspace=workspace))

    assert result.status is TaskRunState.FAILED
    assert repair.attempts == [1, 2]
    assert result.repair_attempts == 2
    assert verifier.calls == 1
    assert len(result.verifications) == 1
    assert result.repairs[-1].progress is not None
    assert result.repairs[-1].progress.has_patch is False
    assert any("repair_progress=NO_PATCH_PRODUCED" in item for item in result.failures[0].evidence)
    assert any(
        "previous_repair_stop_reason=NO_PROGRESS" in item
        for item in repair.failure_evidence[1]
    )
    assert any(
        item.startswith("previous_repair_summary=")
        for item in repair.failure_evidence[1]
    )


@pytest.mark.parametrize(
    ("failure_summaries", "expected_status"),
    [
        (["original assertion", "different assertion"], models.RepairProgressStatus.PROGRESS_MADE),
        (["same assertion", "same assertion"], models.RepairProgressStatus.REPAIR_INEFFECTIVE),
    ],
)
def test_repair_patch_records_failure_signature_progress(
    tmp_path: Path,
    failure_summaries: list[str],
    expected_status: models.RepairProgressStatus,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation completed."),
        ]
    )
    repair = WritesPatchRepair(value=4)
    verifier = SequenceVerifier([False, False], failure_summaries=failure_summaries)
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=verifier,  # type: ignore[arg-type]
        reviewer=agents.ReviewerAgent(driver=FakeDriver([]), model="fake/reviewer"),
        repair=repair,  # type: ignore[arg-type]
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )

    result = asyncio.run(orchestrator.run(_task(max_retries=1), workspace=workspace))

    assert result.status is TaskRunState.FAILED
    assert repair.attempts == [1]
    assert verifier.calls == 2
    progress = result.repairs[0].progress
    assert progress is not None
    assert progress.status is expected_status
    assert progress.has_patch is True
    assert progress.files_changed == ["module.py"]
    assert progress.validation_executed is True
    assert progress.failure_signature_before is not None
    assert progress.failure_signature_after is not None
    if expected_status is models.RepairProgressStatus.PROGRESS_MADE:
        assert progress.failure_signature_before != progress.failure_signature_after
    else:
        assert progress.failure_signature_before == progress.failure_signature_after


def test_verification_failure_resume_verifies_then_repairs_without_developer_replay(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    developer_driver = FakeDriver([])
    repair = WritesPatchRepair(value=2)
    verifier = SequenceVerifier([False, True], failure_summaries=["baseline assertion failed"])
    reviewer_driver = FakeDriver([_response(content=_review_pass())])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=verifier,  # type: ignore[arg-type]
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=repair,  # type: ignore[arg-type]
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )

    result = asyncio.run(
        orchestrator.run(
            _task(max_retries=1),
            workspace=workspace,
            resume_verification_first=True,
        )
    )

    assert result.status is TaskRunState.SUCCEEDED
    assert developer_driver.requests == []
    assert verifier.calls == 2
    assert repair.attempts == [1]
    assert result.developer is None


def test_import_error_produces_targeted_repair_hint() -> None:
    failure = models.FailureReport(
        failure_type=models.FailureType.TEST_FAILURE,
        source=models.FailureSource.VERIFICATION,
        message="Deterministic custom verification failed.",
        retryable=True,
        evidence=[
            "check=custom",
            (
                "stderr=Traceback (most recent call last):\n"
                "ImportError: cannot import name 'GameLogic' "
                "from 'src.gomoku_engine' (/workspace/src/gomoku_engine.py)"
            ),
        ],
    )

    kind, path, symbol = SingleTaskOrchestrator._repair_failure_hint([failure])

    assert kind is models.RepairFailureKind.IMPORT_SYMBOL_MISSING
    assert path == "src/gomoku_engine.py"
    assert symbol == "GameLogic"


def test_state_machine_rejects_invalid_transition() -> None:
    machine = TaskStateMachine()

    with pytest.raises(ValueError, match="invalid task-state transition"):
        machine.transition(TaskRunState.REVIEWING, detail="skip hard gate")



def test_state_machine_allows_bounded_repair_retry_transition() -> None:
    machine = TaskStateMachine()
    machine.transition(TaskRunState.RUNNING, detail="start")
    machine.transition(TaskRunState.VERIFYING, detail="verify")
    machine.transition(TaskRunState.REPAIRING, detail="repair attempt 1")
    machine.transition(TaskRunState.REPAIRING, detail="repair attempt 2")

    assert machine.state is TaskRunState.REPAIRING
    assert machine.events[-1].detail == "repair attempt 2"
