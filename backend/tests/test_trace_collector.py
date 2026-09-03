from uuid import uuid4

import pytest

from app.models.agent import AgentResponse, AgentRole, TokenUsage
from app.models.tools import ToolCall, ToolExecutionResult
from app.models.trace import TaskTraceBatch, TraceBatchSpan, TraceSpanKind, TraceSpanStatus
from app.models.verification import CheckResult, CheckType, VerificationResult
from app.trace.collector import TaskTraceCollector


def test_trace_collector_excludes_sensitive_model_and_tool_payloads() -> None:
    collector = TaskTraceCollector(
        run_id=uuid4(),
        task_id="task-a",
        dispatch_id=uuid4(),
        generation=3,
    )
    response = AgentResponse(
        model="model-v1",
        content="COMPLETION_SECRET_123",
        tool_calls=[
            ToolCall(
                id="call-1",
                name="read_file",
                arguments='{"path":"TOOL_ARGUMENT_SECRET.py"}',
            )
        ],
        usage=TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
        latency_ms=42,
        finish_reason="tool_calls",
    )
    turn_id = collector.record_agent_turn(
        role=AgentRole.DEVELOPER,
        iteration=1,
        response=response,
    )
    collector.record_tool_call(
        role=AgentRole.DEVELOPER,
        iteration=1,
        parent_span_id=turn_id,
        result=ToolExecutionResult(
            tool_call_id="call-1",
            name="read_file",
            ok=True,
            content="TOOL_RESULT_SECRET_456",
        ),
        duration_ms=5,
    )
    collector.record_runtime_progress(
        agent_turn_span_id=turn_id,
        has_workspace_patch=True,
        turn_made_progress=True,
        changed_files_this_turn=("src/feature.py",),
        consecutive_mutation_turns=1,
        same_file_mutation_streak=1,
        convergence_nudge_triggered=True,
    )
    collector.record_verification(
        attempt=1,
        result=VerificationResult(
            passed=True,
            checks=[
                CheckResult(
                    check_type=CheckType.TEST,
                    name="pytest",
                    command="pytest -q",
                    passed=True,
                    exit_code=0,
                    stdout="VERIFIER_STDOUT_SECRET_789",
                    duration_ms=9,
                )
            ],
        ),
        duration_ms=10,
    )

    batch = collector.batch()
    payload = batch.model_dump_json()

    assert batch.generation == 3
    assert [span.kind for span in batch.spans] == [
        TraceSpanKind.AGENT_TURN,
        TraceSpanKind.TOOL_CALL,
        TraceSpanKind.VERIFICATION,
    ]
    assert batch.spans[1].parent_span_id == turn_id
    assert batch.spans[0].total_tokens == 18
    assert batch.spans[0].has_workspace_patch is True
    assert batch.spans[0].turn_made_progress is True
    assert batch.spans[0].changed_files_this_turn == ("src/feature.py",)
    assert batch.spans[0].consecutive_mutation_turns == 1
    assert batch.spans[0].same_file_mutation_streak == 1
    assert batch.spans[0].convergence_nudge_triggered is True
    assert "model-v1" in payload
    assert "read_file" in payload
    assert "COMPLETION_SECRET_123" not in payload
    assert "TOOL_ARGUMENT_SECRET.py" not in payload
    assert "TOOL_RESULT_SECRET_456" not in payload
    assert "VERIFIER_STDOUT_SECRET_789" not in payload


def test_trace_batch_rejects_tool_call_without_preceding_agent_parent() -> None:
    run_id = uuid4()
    with pytest.raises(ValueError, match="parents must precede"):
        TaskTraceBatch(
            run_id=run_id,
            task_id="task-a",
            dispatch_id=uuid4(),
            generation=1,
            spans=(
                TraceBatchSpan(
                    span_id=uuid4(),
                    parent_span_id=uuid4(),
                    kind=TraceSpanKind.TOOL_CALL,
                    ordinal=1,
                    name="tool.read_file",
                    status=TraceSpanStatus.OK,
                    agent_role=AgentRole.DEVELOPER,
                    iteration=1,
                    tool_name="read_file",
                ),
            ),
        )
