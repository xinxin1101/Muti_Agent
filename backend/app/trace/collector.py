from __future__ import annotations

from time import perf_counter
from uuid import UUID, uuid4

from app.models.agent import AgentResponse, AgentRole
from app.models.context import ContextUsage
from app.models.tools import ToolExecutionResult
from app.models.trace import (
    TaskTraceBatch,
    TraceBatchSpan,
    TraceSpanKind,
    TraceSpanStatus,
)
from app.models.verification import VerificationResult


class TaskTraceCollector:
    """In-memory metadata-only trace collector for one queued task generation.

    The collector is deliberately not a control-plane dependency. It records only bounded metadata
    that can later be persisted as TRACE_BATCH sidecar evidence. Prompt/completion text, tool
    arguments/results, repository content and run_token values never enter this object.
    """

    def __init__(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID,
        generation: int,
    ) -> None:
        if generation < 1:
            raise ValueError("trace generation must be positive")
        self._run_id = run_id
        self._task_id = task_id
        self._dispatch_id = dispatch_id
        self._generation = generation
        self._spans: list[TraceBatchSpan] = []

    @staticmethod
    def clock() -> float:
        return perf_counter()

    @staticmethod
    def duration_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))

    @property
    def empty(self) -> bool:
        return not self._spans

    def record_agent_turn(
        self,
        *,
        role: AgentRole,
        iteration: int,
        response: AgentResponse,
        name: str | None = None,
        context_usage: ContextUsage | None = None,
        enable_thinking: bool = False,
        context_compacted_tool_groups: int = 0,
        estimated_prompt_tokens: int = 0,
    ) -> UUID:
        span_id = uuid4()
        self._spans.append(
            TraceBatchSpan(
                span_id=span_id,
                kind=TraceSpanKind.AGENT_TURN,
                ordinal=len(self._spans) + 1,
                name=name or f"{role.value}.model_turn",
                status=TraceSpanStatus.OK,
                duration_ms=response.latency_ms,
                agent_role=role,
                model=response.model,
                iteration=iteration,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                finish_reason=response.finish_reason,
                tool_call_count=len(response.tool_calls),
                enable_thinking=enable_thinking,
                context_estimated_tokens=(
                    context_usage.billable_prompt_tokens if context_usage is not None else 0
                ),
                context_reused_files=context_usage.reused_files if context_usage is not None else 0,
                context_trimmed_files=(
                    context_usage.trimmed_files if context_usage is not None else 0
                ),
                estimated_prompt_tokens=estimated_prompt_tokens,
                context_compacted_tool_groups=context_compacted_tool_groups,
            )
        )
        return span_id

    def record_tool_call(
        self,
        *,
        role: AgentRole,
        iteration: int,
        parent_span_id: UUID,
        result: ToolExecutionResult,
        duration_ms: int,
    ) -> UUID:
        span_id = uuid4()
        self._spans.append(
            TraceBatchSpan(
                span_id=span_id,
                parent_span_id=parent_span_id,
                kind=TraceSpanKind.TOOL_CALL,
                ordinal=len(self._spans) + 1,
                name=f"tool.{result.name}",
                status=TraceSpanStatus.OK if result.ok else TraceSpanStatus.ERROR,
                duration_ms=duration_ms,
                agent_role=role,
                iteration=iteration,
                tool_name=result.name,
                tool_error_code=result.error_code,
            )
        )
        return span_id

    def record_verification(
        self,
        *,
        attempt: int,
        result: VerificationResult,
        duration_ms: int,
    ) -> UUID:
        span_id = uuid4()
        self._spans.append(
            TraceBatchSpan(
                span_id=span_id,
                kind=TraceSpanKind.VERIFICATION,
                ordinal=len(self._spans) + 1,
                name="deterministic_verification",
                status=TraceSpanStatus.OK if result.passed else TraceSpanStatus.ERROR,
                duration_ms=duration_ms,
                attempt=attempt,
                passed=result.passed,
            )
        )
        return span_id

    def batch(self) -> TaskTraceBatch:
        if not self._spans:
            raise ValueError("cannot persist an empty task trace batch")
        return TaskTraceBatch(
            run_id=self._run_id,
            task_id=self._task_id,
            dispatch_id=self._dispatch_id,
            generation=self._generation,
            spans=tuple(self._spans),
        )
