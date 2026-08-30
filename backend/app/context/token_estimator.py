from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.agent import AgentRequest


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


@dataclass(frozen=True)
class TokenEstimator:
    """One provider-neutral estimate with two deliberately separate purposes.

    ``context_window_units`` remains a conservative byte upper bound used only to keep a
    ContextPacket bounded.  ``billable_token_estimate`` is intentionally smaller and is the
    only estimate suitable for a durable cost reservation before provider usage arrives.
    """

    safety_factor: float = 1.15
    ascii_chars_per_token: float = 3.5
    cjk_tokens_per_char: float = 1.2

    def __post_init__(self) -> None:
        if not 1.0 <= self.safety_factor <= 2.0:
            raise ValueError("safety_factor must be between 1.0 and 2.0")
        if not 2.0 <= self.ascii_chars_per_token <= 8.0:
            raise ValueError("ascii_chars_per_token must be between 2.0 and 8.0")
        if not 1.0 <= self.cjk_tokens_per_char <= 2.0:
            raise ValueError("cjk_tokens_per_char must be between 1.0 and 2.0")

    @staticmethod
    def context_window_units(content: str) -> int:
        """Return the conservative UTF-8 upper bound used for context-window safety only."""

        return len(content.encode("utf-8"))

    def billable_token_estimate(self, content: str) -> int:
        """Estimate provider-billed tokens without treating every UTF-8 byte as a token."""

        if not content:
            return 0
        cjk_chars = sum(1 for character in content if _is_cjk(character))
        non_cjk_chars = len(content) - cjk_chars
        raw = (
            cjk_chars * self.cjk_tokens_per_char
            + non_cjk_chars / self.ascii_chars_per_token
        )
        # A non-empty request always needs at least one token; ``int`` avoids float values in
        # durable accounting and the extra 0.999 implements a dependency-free ceiling.
        return max(1, int(raw * self.safety_factor + 0.999))

    def estimate_messages(self, contents: list[str] | tuple[str, ...]) -> int:
        return self.billable_token_estimate("\n".join(contents))

    def estimate_agent_request(self, request: AgentRequest) -> int:
        """Estimate the complete provider payload before a reservation is made.

        Tool definitions and assistant tool-call arguments are provider input too.  Omitting
        them under-reserves the very turns that often contain large ``write_file`` or
        ``apply_patch`` payloads.  This estimate remains advisory; provider ``usage`` is still
        the sole settlement value.
        """

        parts = [f"role={request.role.value}", f"model={request.model}"]
        for message in request.messages:
            parts.extend((
                f"message_role={message.role.value}",
                f"message_content={message.content}",
                f"tool_call_id={message.tool_call_id or ''}",
            ))
            for call in message.tool_calls:
                parts.extend((
                    f"tool_call_id={call.id}",
                    f"tool_call_name={call.name}",
                    f"tool_call_arguments={call.arguments}",
                ))
        for tool in request.tools:
            parameters = json.dumps(
                tool.parameters,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            parts.extend((
                f"tool_name={tool.name}",
                f"tool_description={tool.description}",
                f"tool_parameters={parameters}",
            ))
        return self.estimate_messages(parts)
