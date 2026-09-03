from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent import AgentRole, LivenessCredit, TokenUsage
from app.models.tools import ToolDefinition


class AgentRuntimeStopReason(StrEnum):
    MODEL_STOP = "MODEL_STOP"
    NO_PROGRESS = "NO_PROGRESS"
    REPEATED_TOOL_FAILURE = "REPEATED_TOOL_FAILURE"
    EXPLICIT_BLOCKER = "EXPLICIT_BLOCKER"
    TIME_LIMIT = "TIME_LIMIT"
    TOOL_CALL_LIMIT = "TOOL_CALL_LIMIT"
    ITERATION_LIMIT = "ITERATION_LIMIT"


class ToolProgressKind(StrEnum):
    NONE = "NONE"
    OBSERVATION = "OBSERVATION"
    MUTATION = "MUTATION"
    VERIFICATION = "VERIFICATION"


@dataclass(frozen=True)
class AgentRuntimePolicy:
    role: AgentRole
    model: str
    max_iterations: int
    max_duration_seconds: float
    max_model_turn_seconds: float
    max_tool_calls_per_turn: int
    temperature: float
    max_output_tokens: int
    enable_thinking: bool
    allowed_tool_names: frozenset[str]
    tool_definitions: tuple[ToolDefinition, ...]
    max_retained_tool_groups: int = 2
    max_single_tool_result_tokens: int = 1_200
    max_tool_results_per_turn_tokens: int = 2_400
    mutation_gate_enabled: bool = False
    max_observation_turns_without_mutation: int = 2
    max_mutation_gate_violations: int = 1
    mutation_convergence_enabled: bool = False
    consecutive_mutation_nudge_threshold: int = 3
    same_file_mutation_nudge_threshold: int = 3
    tool_recovery_enabled: bool = False
    tool_recovery_max_output_tokens: int | None = None
    repeated_tool_failure_limit: int = 2
    event_condenser_enabled: bool = True
    stuck_detector_enabled: bool = False
    stuck_action_observation_threshold: int = 4
    stuck_action_error_threshold: int = 3
    stuck_monologue_threshold: int = 3
    stuck_alternating_pattern_threshold: int = 6
    initial_liveness_credit: LivenessCredit = LivenessCredit.INITIAL_STARTUP


class AgentRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stop_reason: AgentRuntimeStopReason
    iterations: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    final_message: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: int = Field(ge=0)
    observation_count: int = Field(default=0, ge=0)
    mutation_count: int = Field(default=0, ge=0)
    mutation_gate_triggered: bool = False
    event_count: int = Field(default=0, ge=0)
    tool_failure_evidence: tuple[str, ...] = Field(default_factory=tuple, max_length=8)