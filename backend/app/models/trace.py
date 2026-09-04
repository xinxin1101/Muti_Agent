from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.agent import AgentRole
from app.models.tools import ToolErrorCode


class TraceSpanKind(StrEnum):
    RUN = "RUN"
    TASK = "TASK"
    DISPATCH = "DISPATCH"
    GENERATION = "GENERATION"
    AGENT_TURN = "AGENT_TURN"
    TOOL_CALL = "TOOL_CALL"
    VERIFICATION = "VERIFICATION"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"
    WORKER_EXECUTION = "WORKER_EXECUTION"
    INTEGRATION = "INTEGRATION"
    FAILURE = "FAILURE"


class TraceSpanStatus(StrEnum):
    OK = "OK"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class TraceSpanSource(StrEnum):
    PERSISTED_RUN = "PERSISTED_RUN"
    TASK_CONTRACT = "TASK_CONTRACT"
    DISPATCH_ATTEMPT = "DISPATCH_ATTEMPT"
    RUNTIME_EVENT = "RUNTIME_EVENT"
    TRACE_BATCH = "TRACE_BATCH"
    TYPED_EVIDENCE = "TYPED_EVIDENCE"


class TraceBatchSpan(BaseModel):
    """Privacy-bounded execution metadata collected inside one queued worker generation.

    Raw prompts, completions, tool arguments, tool results and repository contents are deliberately
    absent. This payload is diagnostic only and is never consumed by success/recovery authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    span_id: UUID
    parent_span_id: UUID | None = None
    kind: TraceSpanKind
    ordinal: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    status: TraceSpanStatus
    duration_ms: int = Field(default=0, ge=0)
    agent_role: AgentRole | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)
    iteration: int | None = Field(default=None, ge=1)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    context_estimated_tokens: int = Field(default=0, ge=0)
    estimated_prompt_tokens: int = Field(default=0, ge=0)
    context_reused_files: int = Field(default=0, ge=0)
    context_trimmed_files: int = Field(default=0, ge=0)
    context_compacted_tool_groups: int = Field(default=0, ge=0)
    finish_reason: str | None = Field(default=None, max_length=128)
    tool_call_count: int | None = Field(default=None, ge=0)
    enable_thinking: bool = False
    has_workspace_patch: bool | None = None
    turn_made_progress: bool | None = None
    changed_files_this_turn: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    consecutive_mutation_turns: int = Field(default=0, ge=0)
    same_file_mutation_streak: int = Field(default=0, ge=0)
    convergence_nudge_triggered: bool = False
    candidate_readiness_known: bool = False
    candidate_ready: bool | None = None
    missing_required_deliverables: tuple[str, ...] = Field(
        default_factory=tuple, max_length=8
    )
    deliverable_progress: bool = False
    deliverable_completion_mode: bool = False
    deliverable_convergence_violations: int = Field(default=0, ge=0)
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    tool_error_code: ToolErrorCode | None = None
    attempt: int | None = Field(default=None, ge=1)
    passed: bool | None = None

    @model_validator(mode="after")
    def validate_kind_shape(self) -> TraceBatchSpan:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("trace token total must equal prompt + completion tokens")

        if self.kind is TraceSpanKind.AGENT_TURN:
            if self.agent_role is None or self.model is None or self.iteration is None:
                raise ValueError("agent-turn trace spans require role, model and iteration")
            if (
                self.tool_name is not None
                or self.tool_error_code is not None
                or self.passed is not None
            ):
                raise ValueError("agent-turn trace spans cannot contain tool/verifier fields")
            return self

        if self.kind is TraceSpanKind.TOOL_CALL:
            if self.parent_span_id is None:
                raise ValueError("tool-call trace spans require an agent-turn parent")
            if self.agent_role is None or self.iteration is None or self.tool_name is None:
                raise ValueError("tool-call trace spans require role, iteration and tool name")
            if self.model is not None or self.passed is not None:
                raise ValueError("tool-call trace spans cannot contain model/verifier fields")
            if self.status is TraceSpanStatus.OK and self.tool_error_code is not None:
                raise ValueError("successful tool-call trace spans cannot contain an error code")
            return self

        if self.kind is TraceSpanKind.VERIFICATION:
            if self.passed is None or self.attempt is None:
                raise ValueError("verification trace spans require pass/fail and attempt")
            if self.agent_role is not None or self.model is not None or self.tool_name is not None:
                raise ValueError("verification trace spans cannot contain agent/tool fields")
            return self

        raise ValueError(
            "trace batches may contain only AGENT_TURN, TOOL_CALL or VERIFICATION spans"
        )


class TaskTraceBatch(BaseModel):
    """One non-authoritative trace sidecar for a queued task generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    dispatch_id: UUID
    generation: int = Field(ge=1)
    spans: tuple[TraceBatchSpan, ...] = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_span_graph(self) -> TaskTraceBatch:
        span_ids = [span.span_id for span in self.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("trace batch span ids must be unique")
        if [span.ordinal for span in self.spans] != list(range(1, len(self.spans) + 1)):
            raise ValueError("trace batch ordinals must be contiguous from one")

        seen: dict[UUID, TraceBatchSpan] = {}
        for span in self.spans:
            if span.parent_span_id is not None:
                parent = seen.get(span.parent_span_id)
                if parent is None:
                    raise ValueError("trace span parents must precede their children")
                if (
                    span.kind is TraceSpanKind.TOOL_CALL
                    and parent.kind is not TraceSpanKind.AGENT_TURN
                ):
                    raise ValueError("tool-call trace spans must descend from an agent turn")
            seen[span.span_id] = span
        return self


class CausalTraceSpan(BaseModel):
    """Read-only correlation span projected from accepted facts plus trace sidecars."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    span_id: UUID
    parent_span_id: UUID | None = None
    run_id: UUID
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    dispatch_id: UUID | None = None
    generation: int | None = Field(default=None, ge=1)
    kind: TraceSpanKind
    status: TraceSpanStatus
    name: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    source: TraceSpanSource
    source_record_id: str = Field(min_length=1, max_length=255)
    evidence_id: int | None = Field(default=None, ge=1)
    runtime_event_id: int | None = Field(default=None, ge=1)
    agent_role: AgentRole | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)
    iteration: int | None = Field(default=None, ge=1)
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    tool_error_code: ToolErrorCode | None = None
    attempt: int | None = Field(default=None, ge=1)
    passed: bool | None = None
    outcome: str | None = Field(default=None, max_length=128)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    context_estimated_tokens: int = Field(default=0, ge=0)
    estimated_prompt_tokens: int = Field(default=0, ge=0)
    context_reused_files: int = Field(default=0, ge=0)
    context_trimmed_files: int = Field(default=0, ge=0)
    context_compacted_tool_groups: int = Field(default=0, ge=0)
    enable_thinking: bool = False
    has_workspace_patch: bool | None = None
    turn_made_progress: bool | None = None
    changed_files_this_turn: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    consecutive_mutation_turns: int = Field(default=0, ge=0)
    same_file_mutation_streak: int = Field(default=0, ge=0)
    convergence_nudge_triggered: bool = False
    candidate_readiness_known: bool = False
    candidate_ready: bool | None = None
    missing_required_deliverables: tuple[str, ...] = Field(
        default_factory=tuple, max_length=8
    )
    deliverable_progress: bool = False
    deliverable_completion_mode: bool = False
    deliverable_convergence_violations: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_correlation(self) -> CausalTraceSpan:
        if self.dispatch_id is not None and self.task_id is None:
            raise ValueError("dispatch-correlated trace spans require task_id")
        if self.generation is not None and self.dispatch_id is None:
            raise ValueError("generation-correlated trace spans require dispatch_id")
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("trace token total must equal prompt + completion tokens")
        return self


class CausalRunTrace(BaseModel):
    """Diagnostic causal tree. Nothing in this DTO is mutation or success authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    root_span_id: UUID
    diagnostic_only: Literal[True] = True
    privacy_mode: Literal["METADATA_ONLY"] = "METADATA_ONLY"
    spans: tuple[CausalTraceSpan, ...] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def validate_tree(self) -> CausalRunTrace:
        by_id = {span.span_id: span for span in self.spans}
        if len(by_id) != len(self.spans):
            raise ValueError("causal trace span ids must be unique")
        root = by_id.get(self.root_span_id)
        if root is None or root.kind is not TraceSpanKind.RUN or root.parent_span_id is not None:
            raise ValueError("causal trace root must be the parentless RUN span")
        if [span.sequence for span in self.spans] != list(range(1, len(self.spans) + 1)):
            raise ValueError("causal trace sequence must be contiguous from one")
        for span in self.spans:
            if span.run_id != self.run_id:
                raise ValueError("all causal trace spans must match run_id")
            if span.parent_span_id is not None and span.parent_span_id not in by_id:
                raise ValueError("causal trace parent span is missing")
        return self
