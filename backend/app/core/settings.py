from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables and `.env`."""

    app_name: str = "DevFlow"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    workspace_root: Path = Path(".devflow/workspaces")
    siliconflow_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="SILICONFLOW_API_KEY",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DEVFLOW_",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""

    return Settings()
