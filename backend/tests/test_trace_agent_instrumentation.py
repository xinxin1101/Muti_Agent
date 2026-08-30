from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.context import ContextPacketBuilder
from app.models import (
    AgentResponse,
    AgentRole,
    CheckResult,
    CheckType,
    FailureReport,
    FailureSource,
    FailureType,
    ReviewOutcome,
    TaskContract,
    ToolCall,
    VerificationResult,
)
from app.models.trace import TraceSpanKind
from app.trace.collector import TaskTraceCollector
from app.workspace import LocalGitWorkspace


class _Driver:
    def __init__(self, responses: list[AgentResponse]) -> None:
        self._responses = list(responses)

    async def complete(self, _request):
        if not self._responses:
            raise AssertionError("unexpected model turn")
        return self._responses.pop(0)


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
    (root / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> TaskContract:
    return TaskContract(
        task_id="TRACE-AGENTS",
        objective="Change VALUE to 2.",
        readable_files=["feature.py"],
        writable_files=["feature.py"],
        acceptance_criteria=["VALUE is 2."],
        verification_commands=["pytest -q"],
        max_retries=2,
    )


def _response(
    *,
    model: str,
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> AgentResponse:
    return AgentResponse(
        model=model,
        content=content,
        tool_calls=tool_calls or [],
        latency_ms=3,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def test_production_agents_emit_metadata_only_generation_spans(tmp_path: Path) -> None:
    asyncio.run(_production_agents_emit_metadata_only_generation_spans(tmp_path))


async def _production_agents_emit_metadata_only_generation_spans(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    context_packet = ContextPacketBuilder().build(task, workspace=workspace)
    collector = TaskTraceCollector(
        run_id=uuid4(),
        task_id=task.task_id,
        dispatch_id=uuid4(),
        generation=3,
    )

    developer = DeveloperAgent(
        driver=_Driver(
            [
                _response(
                    model="test/developer",
                    tool_calls=[
                        ToolCall(
                            id="patch-1",
                            name="apply_patch",
                            arguments=json.dumps(
                                {
                                    "path": "feature.py",
                                    "old_text": "VALUE = 1",
                                    "new_text": "VALUE = 2",
                                }
                            ),
                        )
                    ],
                ),
                _response(
                    model="test/developer",
                    content="developer secret completion",
                ),
            ]
        ),
        model="test/developer",
    )
    await developer.run(
        task,
        workspace=workspace,
        context_packet=context_packet,
        trace=collector,
    )

    verification = VerificationResult(
        passed=True,
        checks=[
            CheckResult(
                check_type=CheckType.TEST,
                name="pytest",
                command="pytest -q",
                passed=True,
                exit_code=0,
            )
        ],
    )
    reviewer = ReviewerAgent(
        driver=_Driver(
            [
                _response(model="test/reviewer", content="invalid reviewer secret"),
                _response(
                    model="test/reviewer",
                    content=json.dumps(
                        {
                            "decision": "PASS",
                            "summary": "Verified diff is semantically acceptable.",
                            "issues": [],
                        }
                    ),
                ),
            ]
        ),
        model="test/reviewer",
        max_schema_repair_attempts=1,
    )
    decision = await reviewer.review(
        task,
        verification,
        workspace=workspace,
        context_packet=context_packet,
        trace=collector,
    )
    assert decision.decision is ReviewOutcome.PASS

    repair = RepairAgent(
        driver=_Driver(
            [
                _response(
                    model="test/repair",
                    content="repair secret completion",
                )
            ]
        ),
        model="test/repair",
    )
    await repair.repair(
        task,
        [
            FailureReport(
                failure_type=FailureType.TEST_FAILURE,
                source=FailureSource.VERIFICATION,
                message="Synthetic retryable failure for trace instrumentation.",
                retryable=True,
                evidence=["check=pytest"],
            )
        ],
        attempt=1,
        workspace=workspace,
        context_packet=context_packet,
        trace=collector,
    )

    batch = collector.batch()
    turns = [span for span in batch.spans if span.kind is TraceSpanKind.AGENT_TURN]
    tools = [span for span in batch.spans if span.kind is TraceSpanKind.TOOL_CALL]

    assert [turn.agent_role for turn in turns] == [
        AgentRole.DEVELOPER,
        AgentRole.DEVELOPER,
        AgentRole.REVIEWER,
        AgentRole.REVIEWER,
        AgentRole.REPAIR,
    ]
    assert len(tools) == 1
    assert tools[0].agent_role is AgentRole.DEVELOPER
    assert tools[0].parent_span_id == turns[0].span_id
    assert turns[3].name == "reviewer.schema_repair_turn"
    assert all(turn.context_estimated_tokens > 0 for turn in turns)
    assert all(turn.context_reused_files == 0 for turn in turns)

    serialized = batch.model_dump_json()
    assert "developer secret completion" not in serialized
    assert "invalid reviewer secret" not in serialized
    assert "repair secret completion" not in serialized
    assert "old_text" not in serialized
    assert "new_text" not in serialized
