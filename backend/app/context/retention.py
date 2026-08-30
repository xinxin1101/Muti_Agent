from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


class AgentWorkingState(BaseModel):
    """Deterministic, non-authoritative memory for one bounded Agent session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    inspected_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    relevant_symbols: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    observed_ranges: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    changed_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    confirmed_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    pending_actions: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    unresolved_questions: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    recent_errors: tuple[str, ...] = Field(default_factory=tuple, max_length=12)


@dataclass(frozen=True)
class ToolCallGroup:
    assistant: AgentMessage
    results: tuple[AgentMessage, ...]


@dataclass
class _StateBuilder:
    task_id: str
    inspected_files: set[str] = field(default_factory=set)
    relevant_symbols: set[str] = field(default_factory=set)
    observed_ranges: set[str] = field(default_factory=set)
    changed_files: set[str] = field(default_factory=set)
    confirmed_facts: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)

    def snapshot(self) -> AgentWorkingState:
        return AgentWorkingState(
            task_id=self.task_id,
            inspected_files=tuple(sorted(self.inspected_files))[:100],
            relevant_symbols=tuple(sorted(self.relevant_symbols))[:100],
            observed_ranges=tuple(sorted(self.observed_ranges))[:100],
            changed_files=tuple(sorted(self.changed_files))[:100],
            confirmed_facts=tuple(self.confirmed_facts[-30:]),
            pending_actions=(),
            unresolved_questions=(),
            recent_errors=tuple(self.recent_errors[-12:]),
        )


class AgentContextRetention:
    """Retain bounded, structurally valid tool groups while raw results stay in Trace."""

    def __init__(
        self,
        *,
        task_id: str,
        base_messages: list[AgentMessage],
        max_retained_tool_groups: int = 2,
    ) -> None:
        if not 1 <= max_retained_tool_groups <= 4:
            raise ValueError("max_retained_tool_groups must be between 1 and 4")
        self._base_messages = list(base_messages)
        self._max_retained_tool_groups = max_retained_tool_groups
        self._groups: list[ToolCallGroup] = []
        self._state = _StateBuilder(task_id=task_id)

    @property
    def compacted_group_count(self) -> int:
        return max(0, len(self._groups) - self._max_retained_tool_groups)

    def add_group(
        self,
        *,
        assistant: AgentMessage,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> None:
        if assistant.role is not MessageRole.ASSISTANT or not assistant.tool_calls:
            raise ValueError("a tool group must start with an assistant tool-call message")
        if tuple(call.id for call in calls) != tuple(call.id for call in assistant.tool_calls):
            raise ValueError("tool group calls must match assistant tool calls")
        if tuple(result.tool_call_id for result in results) != tuple(call.id for call in calls):
            raise ValueError("tool group results must match calls in order")
        messages = tuple(
            AgentMessage(
                role=MessageRole.TOOL,
                content=result.model_dump_json(),
                tool_call_id=result.tool_call_id,
            )
            for result in results
        )
        self._groups.append(ToolCallGroup(assistant=assistant, results=messages))
        for call, result in zip(calls, results, strict=True):
            self._observe(call, result)

    def messages(self) -> list[AgentMessage]:
        retained = self._groups[-self._max_retained_tool_groups :]
        messages = list(self._base_messages)
        if self.compacted_group_count:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER,
                    content=(
                        "DevFlow compact working state (runtime metadata, not evidence):\n"
                        + self._state.snapshot().model_dump_json(exclude_none=True)
                    ),
                )
            )
        for group in retained:
            messages.append(group.assistant)
            messages.extend(group.results)
        return messages

    def _observe(self, call: ToolCall, result: ToolExecutionResult) -> None:
        if not result.ok:
            self._state.recent_errors.append(f"{call.name}: {result.error_code or 'ERROR'}")
            return
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            self._state.confirmed_facts.append(f"{call.name} completed")
            return
        if not isinstance(payload, dict):
            return
        if call.name == "read_files":
            for item in payload.get("files", []):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    self._state.inspected_files.add(item["path"])
        elif isinstance(payload.get("path"), str):
            self._state.inspected_files.add(payload["path"])
        if isinstance(payload.get("symbol"), str):
            self._state.relevant_symbols.add(payload["symbol"])
        if isinstance(payload.get("start_line"), int) and isinstance(payload.get("end_line"), int):
            path = payload.get("path", "")
            self._state.observed_ranges.add(
                f"{path}:{payload['start_line']}-{payload['end_line']}"
            )
        if call.name in {"write_file", "apply_patch"} and isinstance(payload.get("path"), str):
            self._state.changed_files.add(payload["path"])
        if call.name.startswith("search_code"):
            self._state.confirmed_facts.append(f"{call.name} completed")

