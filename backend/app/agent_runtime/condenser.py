from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.integrations.openhands import OpenHandsEventCondenserAdapter
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


class CondensedAgentState(BaseModel):
    """Bounded provider-facing View derived from append-only runtime events."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[AgentMessage, ...] = Field(min_length=1)
    compacted_tool_groups: int = Field(default=0, ge=0)
    event_count: int = Field(default=0, ge=0)
    condensation_count: int = Field(default=0, ge=0)


class AgentCondenser:
    """Runtime condenser boundary with OpenHands-style Event/View semantics by default.

    The event-native backend keeps completed ToolGroup events append-only and appends explicit
    Condensation events when older groups leave the provider-facing View. A feature flag retains
    the previous AgentContextRetention backend strictly as a rollback path during Runtime V3
    validation; the default xin_01 runtime uses the event-native implementation.
    """

    def __init__(
        self,
        *,
        task_id: str,
        base_messages: list[AgentMessage],
        max_retained_tool_groups: int,
        max_single_tool_result_tokens: int,
        max_tool_results_per_turn_tokens: int,
        event_native_enabled: bool = True,
    ) -> None:
        self._event_native_enabled = event_native_enabled
        self._event_backend: OpenHandsEventCondenserAdapter | None = None
        self._legacy_retention = None

        if event_native_enabled:
            self._event_backend = OpenHandsEventCondenserAdapter(
                task_id=task_id,
                base_messages=base_messages,
                max_retained_tool_groups=max_retained_tool_groups,
                max_single_tool_result_tokens=max_single_tool_result_tokens,
                max_tool_results_per_turn_tokens=max_tool_results_per_turn_tokens,
            )
            return

        # Rollback-only import keeps Runtime V3 free of the legacy backend in its normal path.
        from app.context.retention import AgentContextRetention

        self._legacy_retention = AgentContextRetention(
            task_id=task_id,
            base_messages=base_messages,
            max_retained_tool_groups=max_retained_tool_groups,
            max_single_tool_result_tokens=max_single_tool_result_tokens,
            max_tool_results_per_turn_tokens=max_tool_results_per_turn_tokens,
        )

    @property
    def event_count(self) -> int:
        if self._event_backend is not None:
            return self._event_backend.event_count
        return 0

    @property
    def condensation_count(self) -> int:
        if self._event_backend is not None:
            return self._event_backend.condensation_count
        return 0

    def state(self, *, runtime_instruction: str | None = None) -> CondensedAgentState:
        if self._event_backend is not None:
            view = self._event_backend.view()
            messages = list(view.messages)
            if runtime_instruction:
                messages.append(
                    AgentMessage(
                        role=MessageRole.USER,
                        content=runtime_instruction,
                    )
                )
            return CondensedAgentState(
                messages=tuple(messages),
                compacted_tool_groups=view.compacted_tool_groups,
                event_count=view.event_count,
                condensation_count=view.condensation_count,
            )

        assert self._legacy_retention is not None
        messages = self._legacy_retention.messages()
        if runtime_instruction:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER,
                    content=runtime_instruction,
                )
            )
        return CondensedAgentState(
            messages=tuple(messages),
            compacted_tool_groups=self._legacy_retention.compacted_group_count,
        )

    def add_group(
        self,
        *,
        assistant: AgentMessage,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> bool:
        if self._event_backend is not None:
            return self._event_backend.add_group(
                assistant=assistant,
                calls=calls,
                results=results,
            )

        assert self._legacy_retention is not None
        return self._legacy_retention.add_group(
            assistant=assistant,
            calls=calls,
            results=results,
        )
