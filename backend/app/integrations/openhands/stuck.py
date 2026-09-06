from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from app.models.tools import ToolCall, ToolExecutionResult
from app.vendor.openhands.stuck_detector.core import (
    ActionEvent,
    AgentErrorEvent,
    ConversationState,
    MessageEvent,
    ObservationEvent,
    StuckDetectionThresholds,
    StuckDetector,
    StuckPattern,
)


@dataclass(frozen=True)
class OpenHandsStuckDecision:
    reason: StuckPattern | None = None
    nudge: str | None = None

    @property
    def should_stop(self) -> bool:
        return self.reason is not None and self.nudge is None


class OpenHandsStuckAdapter:
    """Translate DevFlow tool activity into bounded OpenHands-style stuck events.

    Raw tool arguments and observations are never retained. Stable SHA-256 signatures preserve
    equality semantics while keeping repository source and large tool results outside the detector.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        action_observation_threshold: int = 4,
        action_error_threshold: int = 3,
        monologue_threshold: int = 3,
        alternating_pattern_threshold: int = 6,
    ) -> None:
        self._enabled = enabled
        self._sequence = 0
        self._state = ConversationState()
        self._detector = StuckDetector(
            self._state,
            thresholds=StuckDetectionThresholds(
                action_observation=action_observation_threshold,
                action_error=action_error_threshold,
                monologue=monologue_threshold,
                alternating_pattern=alternating_pattern_threshold,
            ),
        )
        if enabled:
            self._append_user_boundary()

    def record_model_response(self, *, content: str, has_tool_calls: bool) -> None:
        if not self._enabled or has_tool_calls:
            return
        self._state.events.append(
            MessageEvent(
                sequence=self._next_sequence(),
                source="agent",
                message_signature=self._signature(content),
            )
        )

    def record_tool_result(self, call: ToolCall, result: ToolExecutionResult) -> None:
        if not self._enabled:
            return
        self._state.events.append(
            ActionEvent(
                sequence=self._next_sequence(),
                source="agent",
                action_signature=self._action_signature(call),
                tool_name=call.name,
            )
        )
        if result.ok:
            self._state.events.append(
                ObservationEvent(
                    sequence=self._next_sequence(),
                    source="environment",
                    observation_signature=self._observation_signature(result),
                    tool_name=result.name,
                )
            )
            return
        self._state.events.append(
            AgentErrorEvent(
                sequence=self._next_sequence(),
                source="agent",
                error_signature=self._observation_signature(result),
                error_summary=self._bounded_error_summary(result),
                tool_name=result.name,
            )
        )

    def inspect(self) -> OpenHandsStuckDecision:
        if not self._enabled:
            return OpenHandsStuckDecision()
        nudge = self._detector.get_action_error_nudge()
        if nudge is not None:
            return OpenHandsStuckDecision(
                reason=StuckPattern.REPEATING_ACTION_ERROR,
                nudge=nudge,
            )
        return OpenHandsStuckDecision(reason=self._detector.stuck_reason())

    def _append_user_boundary(self) -> None:
        self._state.events.append(
            MessageEvent(
                sequence=self._next_sequence(),
                source="user",
                message_signature=self._signature("devflow-task-start"),
            )
        )

    def _next_sequence(self) -> int:
        value = self._sequence
        self._sequence += 1
        return value

    @classmethod
    def _action_signature(cls, call: ToolCall) -> str:
        raw = call.arguments or "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            normalized = raw.strip()
        else:
            normalized = json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return cls._signature(f"{call.name}\0{normalized}")

    @classmethod
    def _observation_signature(cls, result: ToolExecutionResult) -> str:
        error_code = result.error_code.value if result.error_code is not None else ""
        content_hash = cls._signature(result.content)
        return cls._signature(
            f"{result.name}\0{int(result.ok)}\0{error_code}\0{content_hash}"
        )

    @staticmethod
    def _bounded_error_summary(result: ToolExecutionResult) -> str:
        error_code = result.error_code.value if result.error_code is not None else "UNKNOWN"
        content = " ".join(result.content.split())
        if len(content) > 240:
            content = content[:237] + "..."
        return f"{error_code}: {content or 'tool call failed'}"

    @staticmethod
    def _signature(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "OpenHandsStuckAdapter",
    "OpenHandsStuckDecision",
    "StuckPattern",
]
