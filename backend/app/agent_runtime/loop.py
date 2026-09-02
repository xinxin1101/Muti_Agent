from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic
from typing import Any

from app.agent_runtime.events import AgentRuntimeEvent, AgentRuntimeEventKind
from app.agent_runtime.progress import ToolProgressClassifier
from app.agent_runtime.types import (
    AgentRuntimePolicy,
    AgentRuntimeResult,
    AgentRuntimeStopReason,
    ToolProgressKind,
)
from app.agent_runtime.view import AgentViewBuilder
from app.context.retention import AgentContextRetention
from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentMessage, AgentRequest, MessageRole, TokenUsage
from app.models.context import ContextUsage
from app.models.tools import ToolErrorCode, ToolExecutionResult
from app.providers.base import AgentDriver
from app.tools import RepositoryToolbox
from app.trace.collector import TaskTraceCollector
from app.workspace import LocalGitWorkspace

_TOOL_EXECUTION_TIMEOUT_SECONDS = 30.0


class AgentLoop:
    """Shared bounded tool-calling loop for DevFlow Agent Runtime V3.

    The loop deliberately separates observation success from repository mutation progress.
    Financial token authority remains in BudgetedAgentDriver; repository authority remains
    in RepositoryToolbox/ScopeEnforcer; correctness authority remains in verification.
    """

    def __init__(
        self,
        *,
        driver: AgentDriver,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._driver = driver
        self._clock = clock
        self._token_estimator = TokenEstimator()

    async def run(
        self,
        *,
        policy: AgentRuntimePolicy,
        task_id: str,
        base_messages: list[AgentMessage],
        toolbox: RepositoryToolbox,
        workspace: LocalGitWorkspace,
        context_estimated_tokens: int = 0,
        context_usage: ContextUsage | None = None,
        trace: TaskTraceCollector | None = None,
    ) -> AgentRuntimeResult:
        retention = AgentContextRetention(
            task_id=task_id,
            base_messages=base_messages,
            max_retained_tool_groups=policy.max_retained_tool_groups,
            max_single_tool_result_tokens=policy.max_single_tool_result_tokens,
            max_tool_results_per_turn_tokens=policy.max_tool_results_per_turn_tokens,
        )
        started_at = self._clock()
        start_patch_hash = workspace.change_snapshot().patch_hash
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_tokens = 0
        total_latency_ms = 0
        tool_call_count = 0
        observation_count = 0
        mutation_count = 0
        observation_turns_without_mutation = 0
        mutation_gate_triggered = False
        mutation_gate_violations = 0
        no_patch_model_stops = 0
        runtime_instruction: str | None = None
        events: list[AgentRuntimeEvent] = []

        for iteration in range(1, policy.max_iterations + 1):
            remaining = policy.max_duration_seconds - (self._clock() - started_at)
            if remaining <= 0:
                return self._result(
                    stop_reason=AgentRuntimeStopReason.TIME_LIMIT,
                    iterations=iteration - 1,
                    tool_calls=tool_call_count,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=total_latency_ms,
                    observation_count=observation_count,
                    mutation_count=mutation_count,
                    mutation_gate_triggered=mutation_gate_triggered,
                    events=events,
                )

            view = AgentViewBuilder.build(
                retention,
                runtime_instruction=runtime_instruction,
            )
            runtime_instruction = None
            patch_changed = workspace.change_snapshot().patch_hash != start_patch_hash
            request = AgentRequest(
                role=policy.role,
                model=policy.model,
                messages=list(view.messages),
                temperature=policy.temperature,
                max_output_tokens=policy.max_output_tokens,
                enable_thinking=policy.enable_thinking,
                budget_progress=patch_changed,
                context_estimated_tokens=context_estimated_tokens,
                execution_iteration=iteration,
                tools=list(policy.tool_definitions),
            )
            try:
                async with asyncio.timeout(
                    min(remaining, policy.max_model_turn_seconds)
                ):
                    response = await self._driver.complete(request)
            except TimeoutError:
                return self._result(
                    stop_reason=AgentRuntimeStopReason.TIME_LIMIT,
                    iterations=iteration - 1,
                    tool_calls=tool_call_count,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=total_latency_ms,
                    observation_count=observation_count,
                    mutation_count=mutation_count,
                    mutation_gate_triggered=mutation_gate_triggered,
                    events=events,
                )

            turn_span_id = None
            if trace is not None:
                turn_span_id = trace.record_agent_turn(
                    role=policy.role,
                    iteration=iteration,
                    response=response,
                    enable_thinking=policy.enable_thinking,
                    context_usage=context_usage,
                    context_compacted_tool_groups=view.compacted_tool_groups,
                    estimated_prompt_tokens=self._token_estimator.estimate_agent_request(
                        request
                    ),
                )

            total_prompt_tokens += response.usage.prompt_tokens
            total_completion_tokens += response.usage.completion_tokens
            total_tokens += response.usage.total_tokens
            total_latency_ms += response.latency_ms
            events.append(
                AgentRuntimeEvent(
                    sequence=len(events),
                    kind=AgentRuntimeEventKind.MODEL_RESPONSE,
                    iteration=iteration,
                    detail=(
                        f"tool_calls={len(response.tool_calls)};"
                        f"finish_reason={response.finish_reason or 'unknown'}"
                    ),
                )
            )

            assistant_message = AgentMessage(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )

            if not response.tool_calls:
                if workspace.change_snapshot().patch_hash != start_patch_hash:
                    return self._result(
                        stop_reason=AgentRuntimeStopReason.MODEL_STOP,
                        iterations=iteration,
                        tool_calls=tool_call_count,
                        final_message=response.content,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        latency_ms=total_latency_ms,
                        observation_count=observation_count,
                        mutation_count=mutation_count,
                        mutation_gate_triggered=mutation_gate_triggered,
                        events=events,
                    )
                if self._is_explicit_blocker(response.content):
                    return self._result(
                        stop_reason=AgentRuntimeStopReason.EXPLICIT_BLOCKER,
                        iterations=iteration,
                        tool_calls=tool_call_count,
                        final_message=response.content,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        latency_ms=total_latency_ms,
                        observation_count=observation_count,
                        mutation_count=mutation_count,
                        mutation_gate_triggered=mutation_gate_triggered,
                        events=events,
                    )
                no_patch_model_stops += 1
                if no_patch_model_stops >= 2 or iteration >= policy.max_iterations:
                    return self._result(
                        stop_reason=AgentRuntimeStopReason.NO_PROGRESS,
                        iterations=iteration,
                        tool_calls=tool_call_count,
                        final_message=response.content,
                        prompt_tokens=total_prompt_tokens,
                        completion_tokens=total_completion_tokens,
                        total_tokens=total_tokens,
                        latency_ms=total_latency_ms,
                        observation_count=observation_count,
                        mutation_count=mutation_count,
                        mutation_gate_triggered=mutation_gate_triggered,
                        events=events,
                    )
                mutation_gate_triggered = True
                runtime_instruction = self._mutation_required_prompt(
                    strict=False,
                    reason="The model stopped without producing a workspace mutation.",
                )
                events.append(
                    AgentRuntimeEvent(
                        sequence=len(events),
                        kind=AgentRuntimeEventKind.MUTATION_GATE,
                        iteration=iteration,
                        progress_kind=ToolProgressKind.NONE,
                        detail="model_stop_without_mutation",
                    )
                )
                continue

            no_patch_model_stops = 0
            if len(response.tool_calls) > policy.max_tool_calls_per_turn:
                return self._result(
                    stop_reason=AgentRuntimeStopReason.TOOL_CALL_LIMIT,
                    iterations=iteration,
                    tool_calls=tool_call_count,
                    final_message=response.content,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=total_latency_ms,
                    observation_count=observation_count,
                    mutation_count=mutation_count,
                    mutation_gate_triggered=mutation_gate_triggered,
                    events=events,
                )

            results: list[ToolExecutionResult] = []
            for call in response.tool_calls:
                tool_started = trace.clock() if trace is not None else 0.0
                if call.name not in policy.allowed_tool_names:
                    result = ToolExecutionResult(
                        tool_call_id=call.id,
                        name=call.name,
                        ok=False,
                        content=(
                            "This tool is not available for this Agent runtime policy."
                        ),
                        error_code=ToolErrorCode.UNKNOWN_TOOL,
                    )
                else:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(toolbox.execute, call),
                            timeout=_TOOL_EXECUTION_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        return self._result(
                            stop_reason=AgentRuntimeStopReason.TIME_LIMIT,
                            iterations=iteration,
                            tool_calls=tool_call_count,
                            prompt_tokens=total_prompt_tokens,
                            completion_tokens=total_completion_tokens,
                            total_tokens=total_tokens,
                            latency_ms=total_latency_ms,
                            observation_count=observation_count,
                            mutation_count=mutation_count,
                            mutation_gate_triggered=mutation_gate_triggered,
                            events=events,
                        )

                tool_call_count += 1
                progress_kind = ToolProgressClassifier.classify(call, result)
                if progress_kind is ToolProgressKind.OBSERVATION:
                    observation_count += 1
                if trace is not None:
                    assert turn_span_id is not None
                    trace.record_tool_call(
                        role=policy.role,
                        iteration=iteration,
                        parent_span_id=turn_span_id,
                        result=result,
                        duration_ms=trace.duration_ms(tool_started),
                    )
                events.append(
                    AgentRuntimeEvent(
                        sequence=len(events),
                        kind=AgentRuntimeEventKind.TOOL_RESULT,
                        iteration=iteration,
                        tool_name=call.name,
                        ok=result.ok,
                        progress_kind=progress_kind,
                        detail=(
                            result.error_code.value
                            if result.error_code is not None
                            else "ok"
                        ),
                    )
                )
                results.append(result)

            compacted_code_mutation = retention.add_group(
                assistant=assistant_message,
                calls=response.tool_calls,
                results=results,
            )
            patch_changed_now = (
                workspace.change_snapshot().patch_hash != start_patch_hash
            )
            progress = ToolProgressClassifier.summarize(
                response.tool_calls,
                results,
            )
            if patch_changed_now:
                mutation_count = max(1, mutation_count + progress.successful_mutation_tool_count)
                observation_turns_without_mutation = 0
                mutation_gate_violations = 0
                mutation_gate_triggered = False
            elif progress.observation_count > 0 and progress.mutation_attempt_count == 0:
                observation_turns_without_mutation += 1
                if (
                    policy.mutation_gate_enabled
                    and observation_turns_without_mutation
                    >= policy.max_observation_turns_without_mutation
                ):
                    if not mutation_gate_triggered:
                        mutation_gate_triggered = True
                        runtime_instruction = self._mutation_required_prompt(
                            strict=False,
                            reason=(
                                "The Agent has accumulated observation evidence but the "
                                "workspace patch hash is still unchanged."
                            ),
                        )
                        events.append(
                            AgentRuntimeEvent(
                                sequence=len(events),
                                kind=AgentRuntimeEventKind.MUTATION_GATE,
                                iteration=iteration,
                                progress_kind=ToolProgressKind.OBSERVATION,
                                detail="observation_limit_reached",
                            )
                        )
                    else:
                        mutation_gate_violations += 1
                        if (
                            mutation_gate_violations
                            > policy.max_mutation_gate_violations
                        ):
                            await self._record_tool_cost_outcome(
                                policy=policy,
                                calls=response.tool_calls,
                                results=results,
                                has_real_progress=False,
                                compacted_code_mutation=compacted_code_mutation,
                            )
                            return self._result(
                                stop_reason=AgentRuntimeStopReason.NO_PROGRESS,
                                iterations=iteration,
                                tool_calls=tool_call_count,
                                final_message=response.content,
                                prompt_tokens=total_prompt_tokens,
                                completion_tokens=total_completion_tokens,
                                total_tokens=total_tokens,
                                latency_ms=total_latency_ms,
                                observation_count=observation_count,
                                mutation_count=mutation_count,
                                mutation_gate_triggered=True,
                                events=events,
                            )
                        runtime_instruction = self._mutation_required_prompt(
                            strict=True,
                            reason=(
                                "A mutation gate is already active and another "
                                "observation-only turn produced no patch."
                            ),
                        )

            await self._record_tool_cost_outcome(
                policy=policy,
                calls=response.tool_calls,
                results=results,
                has_real_progress=patch_changed_now,
                compacted_code_mutation=compacted_code_mutation,
            )

            if self._clock() - started_at >= policy.max_duration_seconds:
                return self._result(
                    stop_reason=AgentRuntimeStopReason.TIME_LIMIT,
                    iterations=iteration,
                    tool_calls=tool_call_count,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_tokens,
                    latency_ms=total_latency_ms,
                    observation_count=observation_count,
                    mutation_count=mutation_count,
                    mutation_gate_triggered=mutation_gate_triggered,
                    events=events,
                )

        return self._result(
            stop_reason=AgentRuntimeStopReason.ITERATION_LIMIT,
            iterations=policy.max_iterations,
            tool_calls=tool_call_count,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_tokens,
            latency_ms=total_latency_ms,
            observation_count=observation_count,
            mutation_count=mutation_count,
            mutation_gate_triggered=mutation_gate_triggered,
            events=events,
        )

    async def _record_tool_cost_outcome(
        self,
        *,
        policy: AgentRuntimePolicy,
        calls: list[Any],
        results: list[ToolExecutionResult],
        has_real_progress: bool,
        compacted_code_mutation: bool,
    ) -> None:
        observer = getattr(self._driver, "record_tool_outcome", None)
        if not callable(observer):
            return
        await observer(
            role=policy.role.value,
            calls=calls,
            results=results,
            has_real_progress=has_real_progress,
            compacted_code_mutation=compacted_code_mutation,
        )

    @staticmethod
    def _is_explicit_blocker(content: str) -> bool:
        return content.lstrip().upper().startswith("BLOCKED:")

    @staticmethod
    def _mutation_required_prompt(*, strict: bool, reason: str) -> str:
        prefix = (
            "MUTATION REQUIRED. "
            if strict
            else "The current task is a code-repair task and no workspace mutation exists yet. "
        )
        return (
            prefix
            + reason
            + " Use apply_patch or write_file to produce a scoped candidate mutation now. "
            "Do not repeat exploratory reads/searches unless a concrete missing fact prevents "
            "a safe edit. If a runtime or scope blocker makes mutation impossible, return "
            "exactly 'BLOCKED: <reason>'."
        )

    @staticmethod
    def _result(
        *,
        stop_reason: AgentRuntimeStopReason,
        iterations: int,
        tool_calls: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: int,
        observation_count: int,
        mutation_count: int,
        mutation_gate_triggered: bool,
        events: list[AgentRuntimeEvent],
        final_message: str = "",
    ) -> AgentRuntimeResult:
        return AgentRuntimeResult(
            stop_reason=stop_reason,
            iterations=iterations,
            tool_calls=tool_calls,
            final_message=final_message,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
            latency_ms=latency_ms,
            observation_count=observation_count,
            mutation_count=mutation_count,
            mutation_gate_triggered=mutation_gate_triggered,
            event_count=len(events),
        )
