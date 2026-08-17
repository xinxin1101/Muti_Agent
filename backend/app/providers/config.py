from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import Settings
from app.models.agent import AgentRole


class RoleModelConfig(BaseModel):
    """Maps DevFlow agent roles to provider model identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    planner: str = Field(min_length=1, max_length=256)
    developer: str = Field(min_length=1, max_length=256)
    reviewer: str = Field(min_length=1, max_length=256)
    repair: str = Field(min_length=1, max_length=256)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RoleModelConfig":
        return cls(
            planner=settings.planner_model,
            developer=settings.developer_model,
            reviewer=settings.reviewer_model,
            repair=settings.repair_model,
        )

    def for_role(self, role: AgentRole) -> str:
        return getattr(self, role.value)
