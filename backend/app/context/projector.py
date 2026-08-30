from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.context import ContextPacket
from app.models.failure import FailureReport


class _ContextView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    objective: str
    acceptance_criteria: tuple[str, ...]
    readable_files: tuple[str, ...]
    writable_files: tuple[str, ...]
    readonly_files: tuple[str, ...]
    repository_summary: str
    repository_map: tuple[str, ...]
    changed_files: tuple[str, ...]


class DeveloperContextView(_ContextView):
    relevant_files: tuple[dict[str, object], ...] = Field(default_factory=tuple)
    continuation_summary: str = ""


class RepairContextView(DeveloperContextView):
    target_failures: tuple[dict[str, object], ...] = Field(default_factory=tuple)


class ReviewerContextView(_ContextView):
    scope_summary: str


class AgentContextProjector:
    """Project authoritative ContextPacket facts into role-minimal model prompts."""

    _MAX_FILES = 6
    _MAX_SNIPPET_CHARS = 3_500

    @classmethod
    def developer(cls, packet: ContextPacket) -> DeveloperContextView:
        return DeveloperContextView(
            **cls._base(packet),
            relevant_files=cls._relevant_files(packet),
            continuation_summary=cls._continuation_summary(packet),
        )

    @classmethod
    def repair(
        cls,
        packet: ContextPacket,
        failures: list[FailureReport] | tuple[FailureReport, ...],
    ) -> RepairContextView:
        return RepairContextView(
            **cls._base(packet),
            relevant_files=cls._relevant_files(packet, prefer_changed=True),
            continuation_summary=cls._continuation_summary(packet),
            target_failures=tuple(
                {
                    "type": failure.failure_type.value,
                    "source": failure.source.value,
                    "message": failure.message,
                    "evidence": tuple(failure.evidence[:8]),
                }
                for failure in failures
            ),
        )

    @classmethod
    def reviewer(cls, packet: ContextPacket) -> ReviewerContextView:
        return ReviewerContextView(
            **cls._base(packet),
            scope_summary=(
                f"writable={', '.join(packet.writable_files)}; "
                f"read_only={', '.join(packet.readonly_files) or '<none>'}; "
                f"changed={', '.join(packet.changed_files) or '<none>'}"
            ),
        )

    @staticmethod
    def _base(packet: ContextPacket) -> dict[str, object]:
        return {
            "task_id": packet.task_id,
            "objective": packet.objective,
            "acceptance_criteria": tuple(packet.acceptance_criteria),
            "readable_files": tuple(packet.readable_files),
            "writable_files": tuple(packet.writable_files),
            "readonly_files": tuple(packet.readonly_files),
            "repository_summary": packet.repository_summary,
            "repository_map": tuple(packet.repository_map[:80]),
            "changed_files": tuple(packet.changed_files),
        }

    @classmethod
    def _relevant_files(
        cls,
        packet: ContextPacket,
        *,
        prefer_changed: bool = False,
    ) -> tuple[dict[str, object], ...]:
        selected = list(packet.selected_files)
        if prefer_changed:
            selected.sort(key=lambda item: (not item.changed, item.path))
        return tuple(
            {
                "path": item.path,
                "changed": item.changed,
                "snippets": tuple(
                    {
                        "start_line": snippet.start_line,
                        "end_line": snippet.end_line,
                        "content": snippet.content[: cls._MAX_SNIPPET_CHARS],
                    }
                    for snippet in item.snippets
                ),
            }
            for item in selected[: cls._MAX_FILES]
        )

    @staticmethod
    def _continuation_summary(packet: ContextPacket) -> str:
        if packet.resume is None:
            return ""
        return "\n".join(
            value
            for value in (
                packet.resume.completed_summary,
                packet.resume.remaining_summary,
                packet.resume.verification_summary,
                packet.resume.failure_summary,
            )
            if value
        )

