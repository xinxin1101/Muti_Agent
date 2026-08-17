from typing import Protocol, runtime_checkable

from app.models.agent import AgentRequest, AgentResponse


@runtime_checkable
class AgentDriver(Protocol):
    """Provider-neutral boundary used by all DevFlow agent roles."""

    async def complete(self, request: AgentRequest) -> AgentResponse:
        """Execute one model completion and return a normalized response."""

        ...
