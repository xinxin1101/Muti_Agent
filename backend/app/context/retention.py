from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.context.token_estimator import TokenEstimator
from app.models.agent import AgentMessage, MessageRole
from app.models.tools import ToolCall, ToolExecutionResult


class AgentWorkingState(BaseModel):
    """Deterministic, non-authoritative memory for one bounded Agent session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=128)
    inspected_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    inspected_file_hashes: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    relevant_symbols: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    observed_ranges: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    changed_files: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    confirmed_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    pending_actions: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    unresolved_questions: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    recent_errors: tuple[str, ...] = Field(default_factory=tuple, max_length=12)
    observations: tuple[ToolObservationDigest, ...] = Field(default_factory=tuple, max_length=40)


class ToolObservationDigest(BaseModel):
    """Bounded, non-authoritative summary of a Tool result used after compaction.

    Source code and raw Tool results deliberately remain out of this digest and out of Trace.
    Git remains code truth and deterministic verification remains correctness evidence.
    """

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


@dataclass(frozen=True)
class ToolCallGroup:
    assistant: AgentMessage
    results: tuple[AgentMessage, ...]


@dataclass
class _StateBuilder:
    task_id: str
    inspected_files: set[str] = field(default_factory=set)
    inspected_file_hashes: dict[str, str] = field(default_factory=dict)
    relevant_symbols: set[str] = field(default_factory=set)
    observed_ranges: set[str] = field(default_factory=set)
    changed_files: set[str] = field(default_factory=set)
    confirmed_facts: list[str] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)
    observations: list[ToolObservationDigest] = field(default_factory=list)

    def snapshot(self) -> AgentWorkingState:
        return AgentWorkingState(
            task_id=self.task_id,
            inspected_files=tuple(sorted(self.inspected_files))[:100],
            inspected_file_hashes=tuple(
                f"{path}:{digest}" for path, digest in sorted(self.inspected_file_hashes.items())
            )[:100],
            relevant_symbols=tuple(sorted(self.relevant_symbols))[:100],
            observed_ranges=tuple(sorted(self.observed_ranges))[:100],
            changed_files=tuple(sorted(self.changed_files))[:100],
            confirmed_facts=tuple(self.confirmed_facts[-30:]),
            pending_actions=(),
            unresolved_questions=(),
            recent_errors=tuple(self.recent_errors[-12:]),
            observations=tuple(self.observations[-40:]),
        )


class AgentContextRetention:
    """Retain bounded, structurally valid tool groups while raw results stay in Trace."""

    def __init__(
        self,
        *,
        task_id: str,
        base_messages: list[AgentMessage],
        max_retained_tool_groups: int = 2,
        max_single_tool_result_tokens: int = 1_200,
        max_tool_results_per_turn_tokens: int = 2_400,
    ) -> None:
        if not 1 <= max_retained_tool_groups <= 4:
            raise ValueError("max_retained_tool_groups must be between 1 and 4")
        if max_single_tool_result_tokens < 128:
            raise ValueError("max_single_tool_result_tokens must be at least 128")
        if max_tool_results_per_turn_tokens < max_single_tool_result_tokens:
            raise ValueError("per-turn tool-result budget must cover one tool result")
        self._base_messages = list(base_messages)
        self._max_retained_tool_groups = max_retained_tool_groups
        self._groups: list[ToolCallGroup] = []
        self._compacted_code_mutation_group_count = 0
        self._compacted_tool_result_group_count = 0
        self._tool_result_truncation_count = 0
        self._max_single_tool_result_tokens = max_single_tool_result_tokens
        self._max_tool_results_per_turn_tokens = max_tool_results_per_turn_tokens
        self._token_estimator = TokenEstimator()
        self._state = _StateBuilder(task_id=task_id)

    @property
    def compacted_group_count(self) -> int:
        return (
            self._compacted_code_mutation_group_count
            + self._compacted_tool_result_group_count
            + max(0, len(self._groups) - self._max_retained_tool_groups)
        )

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
        if assistant.role is not MessageRole.ASSISTANT or not assistant.tool_calls:
            raise ValueError("a tool group must start with an assistant tool-call message")
        if tuple(call.id for call in calls) != tuple(call.id for call in assistant.tool_calls):
            raise ValueError("tool group calls must match assistant tool calls")
        if tuple(result.tool_call_id for result in results) != tuple(call.id for call in calls):
            raise ValueError("tool group results must match calls in order")
        remaining_tool_result_tokens = self._max_tool_results_per_turn_tokens
        bounded_messages: list[AgentMessage] = []
        for result in results:
            message = self._bounded_tool_result_message(
                result=result,
                remaining_tokens=remaining_tool_result_tokens,
            )
            bounded_messages.append(message)
            remaining_tool_result_tokens = max(
                0,
                remaining_tool_result_tokens
                - self._token_estimator.billable_token_estimate(message.content),
            )
        messages = tuple(bounded_messages)
        for call, result in zip(calls, results, strict=True):
            self._observe(call, result)
        # Keep provider tool-call ordering valid: if the structured envelopes alone would
        # exceed the turn allowance, compact this entire completed group into WorkingState
        # rather than keeping orphan or over-budget tool responses.
        result_message_tokens = sum(
            self._token_estimator.billable_token_estimate(message.content)
            for message in messages
        )
        if result_message_tokens > self._max_tool_results_per_turn_tokens:
            self._compacted_tool_result_group_count += 1
            return True
        # A successful write can contain an entire source file in assistant tool-call arguments.
        # The repository and its content hash are now the code truth, so replaying that payload on
        # every later turn is both costly and unnecessary. Omit the whole completed tool group to
        # preserve a valid provider message sequence; AgentWorkingState retains the bounded fact.
        if self._is_successful_code_mutation(calls=calls, results=results):
            self._compacted_code_mutation_group_count += 1
            return True
        self._groups.append(ToolCallGroup(assistant=assistant, results=messages))
        return False

    def _bounded_tool_result_message(
        self, *, result: ToolExecutionResult, remaining_tokens: int
    ) -> AgentMessage:
        payload = result.model_dump(mode="json")
        original = result.content
        # Later results remain structurally valid but are represented by a bounded
        # observation once this turn's result budget has been consumed.
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
            return AgentMessage(
                role=MessageRole.TOOL,
                content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                tool_call_id=result.tool_call_id,
            )
        if self._token_estimator.billable_token_estimate(original) > content_limit:
            payload["content"] = self._bounded_preview(original, content_limit)
            self._tool_result_truncation_count += 1
        return AgentMessage(
            role=MessageRole.TOOL,
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            tool_call_id=result.tool_call_id,
        )

    def _bounded_preview(self, content: str, limit: int) -> str:
        """Keep a useful prefix without exceeding the provider-neutral content budget."""

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
        if low == 0:
            return suffix
        return content[:low] + suffix

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
        source_hash = self._source_hash(call=call, result=result)
        if not result.ok:
            self._state.recent_errors.append(f"{call.name}: {result.error_code or 'ERROR'}")
            self._state.observations.append(
                ToolObservationDigest(
                    tool=call.name,
                    source_hash=source_hash,
                    error=(result.error_code.value if result.error_code is not None else "ERROR"),
                )
            )
            return
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            self._state.confirmed_facts.append(f"{call.name} completed")
            self._state.observations.append(
                ToolObservationDigest(tool=call.name, source_hash=source_hash)
            )
            return
        if not isinstance(payload, dict):
            return
        if call.name == "read_files":
            for item in payload.get("files", []):
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    self._state.inspected_files.add(item["path"])
                    self._state.inspected_file_hashes[item["path"]] = source_hash
        elif isinstance(payload.get("path"), str):
            self._state.inspected_files.add(payload["path"])
            self._state.inspected_file_hashes[payload["path"]] = source_hash
        if isinstance(payload.get("symbol"), str):
            self._state.relevant_symbols.add(payload["symbol"])
        if isinstance(payload.get("returned_start_line"), int) and isinstance(
            payload.get("returned_end_line"), int
        ):
            path = payload.get("path", "")
            self._state.observed_ranges.add(
                f"{path}:{payload['returned_start_line']}-{payload['returned_end_line']}"
            )
        if call.name in {"write_file", "apply_patch"} and isinstance(payload.get("path"), str):
            self._state.changed_files.add(payload["path"])
        if call.name.startswith("search_code"):
            self._state.confirmed_facts.append(f"{call.name} completed")
        requested_range = self._range(payload, "requested")
        returned_range = self._range(payload, "returned")
        self._state.observations.append(
            ToolObservationDigest(
                tool=call.name,
                path=payload.get("path") if isinstance(payload.get("path"), str) else None,
                symbol=payload.get("symbol") if isinstance(payload.get("symbol"), str) else None,
                requested_range=requested_range,
                returned_range=returned_range,
                source_hash=source_hash,
                truncated=bool(payload.get("truncated", False)),
                search_hits=self._search_hits(payload),
            )
        )

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
        hits = []
        for item in matches[:5]:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                hits.append(f"{item['path']}:{item.get('line', '?')}")
        return tuple(hits)

    @staticmethod
    def _is_successful_code_mutation(
        *, calls: list[ToolCall], results: list[ToolExecutionResult]
    ) -> bool:
        return any(
            call.name in {"write_file", "apply_patch"} and result.ok
            for call, result in zip(calls, results, strict=True)
        )

    @staticmethod
    def _source_hash(*, call: ToolCall, result: ToolExecutionResult) -> str:
        """Hash written source locally without retaining it in state or persistence."""

        if result.ok and call.name in {"write_file", "apply_patch"}:
            try:
                payload = json.loads(call.arguments)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                source = payload.get("content", payload.get("new_text"))
                if isinstance(source, str):
                    return sha256(source.encode("utf-8")).hexdigest()
        return sha256(result.content.encode("utf-8")).hexdigest()
