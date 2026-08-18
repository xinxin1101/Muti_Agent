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

    database_url: SecretStr | None = None
    database_echo: bool = False

    siliconflow_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="SILICONFLOW_API_KEY",
    )
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    siliconflow_max_retries: int = Field(default=0, ge=0, le=5)

    planner_model: str = "Pro/zai-org/GLM-4.7"
    developer_model: str = "deepseek-ai/DeepSeek-V3.2"
    reviewer_model: str = "Pro/zai-org/GLM-4.7"
    repair_model: str = "deepseek-ai/DeepSeek-V3.2"

    verification_sandbox_image: str = "devflow-verifier:py311"
    verification_sandbox_cpus: float = Field(default=1.0, ge=0.05, le=32.0)
    verification_sandbox_memory_mb: int = Field(default=512, ge=64, le=32_768)
    verification_sandbox_pids_limit: int = Field(default=128, ge=16, le=2_048)
    verification_sandbox_tmpfs_mb: int = Field(default=128, ge=16, le=4_096)
    verification_sandbox_shm_mb: int = Field(default=64, ge=16, le=1_024)
    verification_sandbox_timeout_seconds: float = Field(default=60.0, ge=0.05, le=600.0)

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
