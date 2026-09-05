"""Standalone adaptation of OpenHands stuck detection for vendored use.

Derived from OpenHands software-agent-sdk stuck_detector.py at the pinned commit documented in
UPSTREAM.md. The original source depends on ConversationState and SDK Event classes; this module
keeps the same bounded-window thresholds and pattern semantics over tiny local event records so
DevFlow can reuse the algorithm without importing the whole OpenHands conversation runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION = 20


class StuckPattern(StrEnum):
    REPEATING_ACTION_OBSERVATION = "REPEATING_ACTION_OBSERVATION"
    REPEATING_ACTION_ERROR = "REPEATING_ACTION_ERROR"
    MONOLOGUE = "MONOLOGUE"
    ALTERNATING_ACTION_OBSERVATION = "ALTERNATING_ACTION_OBSERVATION"


@dataclass(frozen=True)
class StuckDetectionThresholds:
    action_observation: int = 4
    action_error: int = 3
    monologue: int = 3
    alternating_pattern: int = 6

    def __post_init__(self) -> None:
        for name, value in (
            ("action_observation", self.action_observation),
            ("action_error", self.action_error),
            ("monologue", self.monologue),
            ("alternating_pattern", self.alternating_pattern),
        ):
            if value < 1:
                raise ValueError(f"{name} threshold must be at least 1")


@dataclass(frozen=True)
class ActionEvent:
    sequence: int
    source: str
    action_signature: str
    tool_name: str


@dataclass(frozen=True)
class ObservationEvent:
    sequence: int
    source: str
    observation_signature: str
    tool_name: str


@dataclass(frozen=True)
class AgentErrorEvent:
    sequence: int
    source: str
    error_signature: str
    error_summary: str
    tool_name: str


@dataclass(frozen=True)
class MessageEvent:
    sequence: int
    source: str
    message_signature: str


@dataclass(frozen=True)
class CondensationSummaryEvent:
    sequence: int


Event = (
    ActionEvent
    | ObservationEvent
    | AgentErrorEvent
    | MessageEvent
    | CondensationSummaryEvent
)


@dataclass
class ConversationState:
    events: list[Event] = field(default_factory=list)

    def active_branch(self, limit: int | None = None) -> list[Event]:
        if limit is None:
            return list(self.events)
        return list(self.events[-limit:])


class StuckDetector:
    """OpenHands-derived bounded detector for repetitive or unproductive patterns."""

    def __init__(
        self,
        state: ConversationState,
        thresholds: StuckDetectionThresholds | None = None,
    ) -> None:
        self.state = state
        self.thresholds = thresholds or StuckDetectionThresholds()
        self._last_nudged_error_event_sequence: int | None = None

    def _events_since_last_user_message(self) -> list[Event]:
        events = self.state.active_branch(limit=MAX_EVENTS_TO_SCAN_FOR_STUCK_DETECTION)
        last_user_index = next(
            (
                index
                for index in reversed(range(len(events)))
                if isinstance(events[index], MessageEvent)
                and events[index].source == "user"
            ),
            -1,
        )
        if last_user_index != -1:
            return events[last_user_index + 1 :]
        return events

    @staticmethod
    def _collect_actions_and_observations(
        events: list[Event],
        max_needed: int,
    ) -> tuple[list[ActionEvent], list[ObservationEvent | AgentErrorEvent]]:
        actions: list[ActionEvent] = []
        observations: list[ObservationEvent | AgentErrorEvent] = []
        for event in reversed(events):
            if isinstance(event, ActionEvent) and len(actions) < max_needed:
                actions.append(event)
            elif isinstance(event, (ObservationEvent, AgentErrorEvent)):
                if len(observations) < max_needed:
                    observations.append(event)
            if len(actions) >= max_needed and len(observations) >= max_needed:
                break
        return actions, observations

    def stuck_reason(self) -> StuckPattern | None:
        events = self._events_since_last_user_message()
        minimum = min(
            self.thresholds.action_observation,
            self.thresholds.action_error,
            self.thresholds.monologue,
        )
        if len(events) < minimum:
            return None

        max_needed = max(
            self.thresholds.action_observation,
            self.thresholds.action_error + 1,
            self.thresholds.alternating_pattern,
        )
        actions, observations = self._collect_actions_and_observations(
            events,
            max_needed,
        )
        if self._action_error_streak(actions, observations) > self.thresholds.action_error:
            return StuckPattern.REPEATING_ACTION_ERROR
        if self._repeating_action_observation(actions, observations):
            return StuckPattern.REPEATING_ACTION_OBSERVATION
        if self._monologue(events):
            return StuckPattern.MONOLOGUE
        if (
            len(events) >= self.thresholds.alternating_pattern
            and self._alternating_action_observation(events)
        ):
            return StuckPattern.ALTERNATING_ACTION_OBSERVATION
        return None

    def is_stuck(self) -> bool:
        return self.stuck_reason() is not None

    def _repeating_action_observation(
        self,
        actions: list[ActionEvent],
        observations: list[ObservationEvent | AgentErrorEvent],
    ) -> bool:
        threshold = self.thresholds.action_observation
        if len(actions) < threshold or len(observations) < threshold:
            return False
        return all(
            self._event_eq(actions[0], action)
            for action in actions[:threshold]
        ) and all(
            self._event_eq(observations[0], observation)
            for observation in observations[:threshold]
        )

    def _action_error_streak(
        self,
        actions: list[ActionEvent],
        observations: list[ObservationEvent | AgentErrorEvent],
    ) -> int:
        if not actions or not observations:
            return 0
        reference = actions[0]
        streak = 0
        for action, observation in zip(actions, observations):
            if not self._event_eq(reference, action):
                break
            if not isinstance(observation, AgentErrorEvent):
                break
            streak += 1
        return streak

    def get_action_error_nudge(self) -> str | None:
        events = self._events_since_last_user_message()
        threshold = self.thresholds.action_error
        actions, observations = self._collect_actions_and_observations(
            events,
            threshold + 1,
        )
        if self._action_error_streak(actions, observations) != threshold:
            return None
        action = actions[0]
        error = observations[0]
        if not isinstance(error, AgentErrorEvent):
            return None
        if error.sequence == self._last_nudged_error_event_sequence:
            return None
        self._last_nudged_error_event_sequence = error.sequence
        return (
            f"You've called `{action.tool_name}` with the same arguments "
            f"{threshold} times in a row and gotten the same error each time: "
            f"{error.error_summary}. Repeating the exact same call again will not work; "
            "correct the arguments or try a different approach."
        )

    def _monologue(self, events: list[Event]) -> bool:
        threshold = self.thresholds.monologue
        if len(events) < threshold:
            return False
        count = 0
        for event in reversed(events):
            if isinstance(event, MessageEvent):
                if event.source == "agent":
                    count += 1
                elif event.source == "user":
                    break
            elif isinstance(event, CondensationSummaryEvent):
                continue
            else:
                break
        return count >= threshold

    def _alternating_action_observation(self, events: list[Event]) -> bool:
        threshold = self.thresholds.alternating_pattern
        actions: list[ActionEvent] = []
        observations: list[ObservationEvent | AgentErrorEvent] = []
        for event in reversed(events):
            if isinstance(event, ActionEvent) and len(actions) < threshold:
                actions.append(event)
            elif isinstance(event, (ObservationEvent, AgentErrorEvent)):
                if len(observations) < threshold:
                    observations.append(event)
            if len(actions) == threshold and len(observations) == threshold:
                break
        if len(actions) != threshold or len(observations) != threshold:
            return False
        actions_equal = all(
            self._event_eq(actions[index], actions[index + 2])
            for index in range(threshold - 2)
        )
        observations_equal = all(
            self._event_eq(observations[index], observations[index + 2])
            for index in range(threshold - 2)
        )
        return actions_equal and observations_equal

    @staticmethod
    def _event_eq(event1: Event, event2: Event) -> bool:
        if type(event1) is not type(event2):
            return False
        if isinstance(event1, ActionEvent) and isinstance(event2, ActionEvent):
            return (
                event1.source == event2.source
                and event1.action_signature == event2.action_signature
                and event1.tool_name == event2.tool_name
            )
        if isinstance(event1, ObservationEvent) and isinstance(
            event2,
            ObservationEvent,
        ):
            return (
                event1.source == event2.source
                and event1.observation_signature == event2.observation_signature
                and event1.tool_name == event2.tool_name
            )
        if isinstance(event1, AgentErrorEvent) and isinstance(
            event2,
            AgentErrorEvent,
        ):
            return (
                event1.source == event2.source
                and event1.error_signature == event2.error_signature
            )
        if isinstance(event1, MessageEvent) and isinstance(event2, MessageEvent):
            return (
                event1.source == event2.source
                and event1.message_signature == event2.message_signature
            )
        return event1 == event2