from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_REPOSITORY_ENV_FILE = _REPOSITORY_ROOT / ".env"


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables and repository `.env`."""

    app_name: str = "DevFlow"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    workspace_root: Path = Path(".devflow/workspaces")

    database_url: SecretStr | None = None
    database_echo: bool = False

    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    dramatiq_namespace: str = Field(default="devflow", min_length=1, max_length=64)
    dramatiq_queue_name: str = Field(default="devflow_tasks", min_length=1, max_length=128)
    worker_id: str | None = Field(default=None, min_length=1, max_length=255)
    worker_lease_seconds: float = Field(default=60.0, gt=0.0, le=86_400.0)
    worker_heartbeat_interval_seconds: float = Field(default=15.0, gt=0.0, le=3_600.0)

    siliconflow_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="SILICONFLOW_API_KEY",
    )
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    siliconflow_max_retries: int = Field(default=0, ge=0, le=5)

    github_token: SecretStr | None = None
    github_publication_timeout_seconds: float = Field(default=30.0, gt=0.0, le=30.0)

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
        env_file=_REPOSITORY_ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="DEVFLOW_",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_worker_lease_cadence(self) -> Self:
        if self.worker_heartbeat_interval_seconds >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat interval must be shorter than the lease duration")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""

    return Settings()
