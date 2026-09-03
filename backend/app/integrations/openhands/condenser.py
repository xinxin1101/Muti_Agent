from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


class ToolObservationDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, max_length=500)
    symbol: str | None = Field(default=None, max_length=300)
    requested_range: str | None = Field(default=None, max_length=64)
    returned_range: str | None = Field(default=None, max_length=64)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool = False
    error: str | None = Field(default=None, max_length=128)
    search_hits: tuple[str, ...] = Field(default_factory=tuple, max_length=5)


class CondensedWorkingState(BaseModel):
    """Deterministic summary of events removed from the provider-facing View."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    inspected_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    inspected_file_hashes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    relevant_symbols: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    observed_ranges: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    changed_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    confirmed_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    recent_errors: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    observations: tuple[ToolObservationDigest, ...] = Field(default_factory=tuple, max_length=40)


@dataclass(frozen=True)
class WorkingStateDelta:
    inspected_files: tuple[str, ...] = ()
    inspected_file_hashes: tuple[tuple[str, str], ...] = ()
    relevant_symbols: tuple[str, ...] = ()
    observed_ranges: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    confirmed_facts: tuple[str, ...] = ()
    recent_errors: tuple[str, ...] = ()
    observations: tuple[ToolObservationDigest, ...] = ()


@dataclass(frozen=True)
class ToolGroupEvent:
    event_id: int
    assistant: AgentMessage
    results: tuple[AgentMessage, ...]
    delta: WorkingStateDelta
    successful_code_mutation: bool
    result_over_budget: bool


@dataclass(frozen=True)
class CondensationEvent:
    """OpenHands-style event that changes the View without deleting prior events."""

    event_id: int
    forgotten_event_ids: frozenset[int]
    summary: str
    summary_offset: int = 0


CondenserEvent = ToolGroupEvent | CondensationEvent


@dataclass
class _WorkingStateBuilder:
    task_id: str
    inspected_files: set[str] = field(default_factory=set)
    inspected_file_hashes: dict[str, str] = field(default_factory=dict)
    relevant_symbols: set[str] = field(default_factory=set)
    observed_ranges: set[str] = field(default_factory=set)
    changed_files: set[str] = field(default_factory=set)
    confirmed_facts: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)
    observations: list[ToolObservationDigest] = field(default_factory=list)

    def apply(self, delta: WorkingStateDelta) -> None:
        self.inspected_files.update(delta.inspected_files)
        self.inspected_file_hashes.update(dict(delta.inspected_file_hashes))
        self.relevant_symbols.update(delta.relevant_symbols)
        self.observed_ranges.update(delta.observed_ranges)
        self.changed_files.update(delta.changed_files)
        self.confirmed_facts.extend(delta.confirmed_facts)
        self.recent_errors.extend(delta.recent_errors)
        self.observations.extend(delta.observations)

    def snapshot(self) -> CondensedWorkingState:
        return CondensedWorkingState(
            task_id=self.task_id,
            inspected_files=tuple(sorted(self.inspected_files))[:100],
            inspected_file_hashes=tuple(
                f"{path}:{digest}"
                for path, digest in sorted(self.inspected_file_hashes.items())
            )[:100],
            relevant_symbols=tuple(sorted(self.relevant_symbols))[:100],
            observed_ranges=tuple(sorted(self.observed_ranges))[:100],
            changed_files=tuple(sorted(self.changed_files))[:100],
            confirmed_facts=tuple(self.confirmed_facts[-30:]),
            recent_errors=tuple(self.recent_errors[-12:]),
            observations=tuple(self.observations[-40:]),
        )


@dataclass(frozen=True)
class OpenHandsCondenserView:
    messages: tuple[AgentMessage, ...]
    compacted_tool_groups: int
    event_count: int
    condensation_count: int


class OpenHandsEventCondenserAdapter:
    """Event-native deterministic condenser following OpenHands View/Condensation semantics.

    Events are append-only. A CondensationEvent marks completed tool groups as forgotten for the
    provider-facing View and inserts a deterministic working-state summary. DevFlow deliberately
    does not call a second summarization LLM here: provider/token-budget authority stays with the
    existing Agent driver while Git and deterministic verification remain the evidence authorities.
    """

    def __init__(
        self,
        *,
        task_id: str,
        base_messages: list[AgentMessage],
        max_retained_tool_groups: int,
        max_single_tool_result_tokens: int,
        max_tool_results_per_turn_tokens: int,
    ) -> None:
        if not 1 <= max_retained_tool_groups <= 4:
            raise ValueError("max_retained_tool_groups must be between 1 and 4")
        if max_single_tool_result_tokens < 128:
            raise ValueError("max_single_tool_result_tokens must be at least 128")
        if max_tool_results_per_turn_tokens < max_single_tool_result_tokens:
            raise ValueError("per-turn tool-result budget must cover one tool result")
        self._base_messages = tuple(base_messages)
        self._max_retained_tool_groups = max_retained_tool_groups
        self._max_single_tool_result_tokens = max_single_tool_result_tokens
        self._max_tool_results_per_turn_tokens = max_tool_results_per_turn_tokens
        self._token_estimator = TokenEstimator()
        self._events: list[CondenserEvent] = []
        self._forgotten_group_ids: set[int] = set()
        self._condensed_state = _WorkingStateBuilder(task_id=task_id)
        self._tool_result_truncation_count = 0
        self._next_event_id = 1

    @property
    def compacted_group_count(self) -> int:
        return len(self._forgotten_group_ids)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def condensation_count(self) -> int:
        return sum(isinstance(event, CondensationEvent) for event in self._events)

    @property
    def tool_result_truncation_count(self) -> int:
        return self._tool_result_truncation_count

    def add_group(
        self,
        *,
        assistant: AgentMessage,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> bool:
        self._validate_group(assistant=assistant, calls=calls, results=results)
        bounded_results = self._bounded_result_messages(results)
        result_tokens = sum(
            self._token_estimator.billable_token_estimate(message.content)
            for message in bounded_results
        )
        event = ToolGroupEvent(
            event_id=self._allocate_event_id(),
            assistant=assistant,
            results=tuple(bounded_results),
            delta=self._build_delta(calls=calls, results=results),
            successful_code_mutation=self._is_successful_code_mutation(
                calls=calls,
                results=results,
            ),
            result_over_budget=result_tokens > self._max_tool_results_per_turn_tokens,
        )
        self._events.append(event)

        forget_now: set[int] = set()
        if event.successful_code_mutation or event.result_over_budget:
            forget_now.add(event.event_id)

        visible_groups = [
            item
            for item in self._events
            if isinstance(item, ToolGroupEvent)
            and item.event_id not in self._forgotten_group_ids
            and item.event_id not in forget_now
        ]
        excess = max(0, len(visible_groups) - self._max_retained_tool_groups)
        if excess:
            forget_now.update(item.event_id for item in visible_groups[:excess])

        new_forgotten = forget_now - self._forgotten_group_ids
        if new_forgotten:
            self._append_condensation(new_forgotten)

        return event.event_id in new_forgotten

    def view(self) -> OpenHandsCondenserView:
        messages = list(self._base_messages)
        latest_summary: str | None = None
        forgotten: set[int] = set()
        visible: list[ToolGroupEvent] = []

        for event in self._events:
            if isinstance(event, ToolGroupEvent):
                if event.event_id not in forgotten:
                    visible.append(event)
                continue

            forgotten.update(event.forgotten_event_ids)
            visible = [group for group in visible if group.event_id not in forgotten]
            latest_summary = event.summary

        if latest_summary:
            messages.append(
                AgentMessage(
                    role=MessageRole.USER,
                    content=latest_summary,
                )
            )
        for group in visible:
            messages.append(group.assistant)
            messages.extend(group.results)

        return OpenHandsCondenserView(
            messages=tuple(messages),
            compacted_tool_groups=len(forgotten),
            event_count=len(self._events),
            condensation_count=self.condensation_count,
        )

    def _append_condensation(self, forgotten_ids: set[int]) -> None:
        groups = {
            event.event_id: event
            for event in self._events
            if isinstance(event, ToolGroupEvent)
        }
        for event_id in sorted(forgotten_ids):
            self._condensed_state.apply(groups[event_id].delta)
        self._forgotten_group_ids.update(forgotten_ids)
        summary = (
            "DevFlow condensed working state (runtime metadata, not evidence):\n"
            + self._condensed_state.snapshot().model_dump_json(exclude_none=True)
        )
        self._events.append(
            CondensationEvent(
                event_id=self._allocate_event_id(),
                forgotten_event_ids=frozenset(forgotten_ids),
                summary=summary,
                summary_offset=0,
            )
        )

    def _bounded_result_messages(
        self,
        results: list[ToolExecutionResult],
    ) -> list[AgentMessage]:
        remaining = self._max_tool_results_per_turn_tokens
        messages: list[AgentMessage] = []
        for result in results:
            message = self._bounded_tool_result_message(
                result=result,
                remaining_tokens=remaining,
            )
            messages.append(message)
            remaining = max(
                0,
                remaining - self._token_estimator.billable_token_estimate(message.content),
            )
        return messages

    def _bounded_tool_result_message(
        self,
        *,
        result: ToolExecutionResult,
        remaining_tokens: int,
    ) -> AgentMessage:
        payload = result.model_dump(mode="json")
        original = result.content
        payload["content"] = ""
        envelope_tokens = self._token_estimator.billable_token_estimate(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        content_limit = min(
            self._max_single_tool_result_tokens,
            max(0, remaining_tokens - envelope_tokens),
        )
        if content_limit < 128:
            payload["content"] = (
                "[DevFlow omitted this tool result from the Agent context because the "
                "per-turn result budget is exhausted; use a scoped read/search command.]"
            )
            self._tool_result_truncation_count += 1
        elif self._token_estimator.billable_token_estimate(original) > content_limit:
            payload["content"] = self._bounded_preview(original, content_limit)
            self._tool_result_truncation_count += 1
        else:
            payload["content"] = original
        return AgentMessage(
            role=MessageRole.TOOL,
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=result.tool_call_id,
        )

    def _bounded_preview(self, content: str, limit: int) -> str:
        suffix = (
            "\n[DevFlow truncated this tool result for the Agent context; "
            "use a scoped read/search command for more detail.]"
        )
        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = content[:middle] + suffix
            if self._token_estimator.billable_token_estimate(candidate) <= limit:
                low = middle
            else:
                high = middle - 1
        return suffix if low == 0 else content[:low] + suffix

    def _build_delta(
        self,
        *,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> WorkingStateDelta:
        inspected_files: set[str] = set()
        inspected_hashes: dict[str, str] = {}
        symbols: set[str] = set()
        ranges: set[str] = set()
        changed_files: set[str] = set()
        facts: list[str] = []
        errors: list[str] = []
        observations: list[ToolObservationDigest] = []

        for call, result in zip(calls, results, strict=True):
            source_hash = self._source_hash(call=call, result=result)
            if not result.ok:
                error = result.error_code.value if result.error_code is not None else "ERROR"
                errors.append(f"{call.name}: {error}")
                observations.append(
                    ToolObservationDigest(
                        tool=call.name,
                        source_hash=source_hash,
                        error=error,
                    )
                )
                continue

            try:
                payload = json.loads(result.content)
            except json.JSONDecodeError:
                facts.append(f"{call.name} completed")
                observations.append(
                    ToolObservationDigest(tool=call.name, source_hash=source_hash)
                )
                continue
            if not isinstance(payload, dict):
                continue

            paths = self._payload_paths(call.name, payload)
            for path in paths:
                inspected_files.add(path)
                inspected_hashes[path] = source_hash
            if call.name in {"write_file", "apply_patch"}:
                changed_files.update(paths)
            symbol = payload.get("symbol")
            if isinstance(symbol, str):
                symbols.add(symbol)
            returned_range = self._range(payload, "returned")
            if returned_range:
                path = paths[0] if paths else ""
                ranges.add(f"{path}:{returned_range}")
            if call.name.startswith("search_code"):
                facts.append(f"{call.name} completed")
            observations.append(
                ToolObservationDigest(
                    tool=call.name,
                    path=paths[0] if paths else None,
                    symbol=symbol if isinstance(symbol, str) else None,
                    requested_range=self._range(payload, "requested"),
                    returned_range=returned_range,
                    source_hash=source_hash,
                    truncated=bool(payload.get("truncated", False)),
                    search_hits=self._search_hits(payload),
                )
            )

        return WorkingStateDelta(
            inspected_files=tuple(sorted(inspected_files)),
            inspected_file_hashes=tuple(sorted(inspected_hashes.items())),
            relevant_symbols=tuple(sorted(symbols)),
            observed_ranges=tuple(sorted(ranges)),
            changed_files=tuple(sorted(changed_files)),
            confirmed_facts=tuple(facts),
            recent_errors=tuple(errors),
            observations=tuple(observations),
        )

    @staticmethod
    def _payload_paths(tool_name: str, payload: dict[str, object]) -> tuple[str, ...]:
        paths: list[str] = []
        if tool_name == "read_files":
            files = payload.get("files")
            if isinstance(files, list):
                for item in files:
                    if isinstance(item, dict) and isinstance(item.get("path"), str):
                        paths.append(item["path"])
        path = payload.get("path")
        if isinstance(path, str):
            paths.append(path)
        patch_paths = payload.get("paths")
        if isinstance(patch_paths, list):
            paths.extend(item for item in patch_paths if isinstance(item, str))
        return tuple(dict.fromkeys(paths))

    @staticmethod
    def _range(payload: dict[str, object], prefix: str) -> str | None:
        start = payload.get(f"{prefix}_start_line")
        end = payload.get(f"{prefix}_end_line")
        return f"{start}-{end}" if isinstance(start, int) and isinstance(end, int) else None

    @staticmethod
    def _search_hits(payload: dict[str, object]) -> tuple[str, ...]:
        matches = payload.get("matches")
        if not isinstance(matches, list):
            return ()
        hits: list[str] = []
        for item in matches[:5]:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                hits.append(f"{item['path']}:{item.get('line', '?')}")
        return tuple(hits)

    @staticmethod
    def _is_successful_code_mutation(
        *,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> bool:
        return any(
            call.name in {"write_file", "apply_patch"} and result.ok
            for call, result in zip(calls, results, strict=True)
        )

    @staticmethod
    def _source_hash(*, call: ToolCall, result: ToolExecutionResult) -> str:
        if result.ok and call.name in {"write_file", "apply_patch"}:
            try:
                payload = json.loads(call.arguments)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                source = payload.get("content", payload.get("new_text", payload.get("patch")))
                if isinstance(source, str):
                    return sha256(source.encode("utf-8")).hexdigest()
        return sha256(result.content.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_group(
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

    def _allocate_event_id(self) -> int:
        value = self._next_event_id
        self._next_event_id += 1
        return value


__all__ = [
    "CondensationEvent",
    "CondensedWorkingState",
    "OpenHandsCondenserView",
    "OpenHandsEventCondenserAdapter",
    "ToolGroupEvent",
]
