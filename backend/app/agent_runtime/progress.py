from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.types import ToolProgressKind
from app.models.tools import ToolCall, ToolExecutionResult

_OBSERVATION_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "read_files",
        "read_range",
        "read_symbol",
        "search_code",
        "search_code_many",
    }
)
_MUTATION_TOOLS = frozenset({"write_file", "apply_patch"})


class ToolProgressSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_count: int = Field(default=0, ge=0)
    mutation_attempt_count: int = Field(default=0, ge=0)
    successful_mutation_tool_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)


class ToolProgressClassifier:
    """Separate successful observation from repository mutation progress."""

    @staticmethod
    def classify(call: ToolCall, result: ToolExecutionResult) -> ToolProgressKind:
        if not result.ok:
            return ToolProgressKind.NONE
        if call.name in _MUTATION_TOOLS:
            return ToolProgressKind.MUTATION
        if call.name in _OBSERVATION_TOOLS:
            return ToolProgressKind.OBSERVATION
        return ToolProgressKind.NONE

    @classmethod
    def summarize(
        cls,
        calls: list[ToolCall],
        results: list[ToolExecutionResult],
    ) -> ToolProgressSummary:
        observations = 0
        mutation_attempts = 0
        successful_mutations = 0
        failed = 0
        for call, result in zip(calls, results, strict=True):
            if not result.ok:
                failed += 1
            if call.name in _MUTATION_TOOLS:
                mutation_attempts += 1
            kind = cls.classify(call, result)
            if kind is ToolProgressKind.OBSERVATION:
                observations += 1
            elif kind is ToolProgressKind.MUTATION:
                successful_mutations += 1
        return ToolProgressSummary(
            observation_count=observations,
            mutation_attempt_count=mutation_attempts,
            successful_mutation_tool_count=successful_mutations,
            failed_count=failed,
        )
