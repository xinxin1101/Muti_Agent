from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
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

    redis_url: SecretStr = SecretStr("redis://127.0.0.1:6379/0")
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

    # Read and publication credentials are distinct capabilities. The legacy DEVFLOW_GITHUB_TOKEN
    # alias remains accepted for publication-only compatibility.
    github_read_token: SecretStr | None = None
    github_publication_token: SecretStr | None = None
    github_token: SecretStr | None = None
    github_publication_timeout_seconds: float = Field(default=30.0, gt=0.0, le=30.0)

    # /readyz validates configured model ids against the provider catalogue instead of assuming
    # that a historical default remains available forever.
    planner_model: str = "zai-org/GLM-5.2"
    developer_model: str = "Pro/deepseek-ai/DeepSeek-V3.2"
    reviewer_model: str = "zai-org/GLM-5.2"
    repair_model: str = "Pro/deepseek-ai/DeepSeek-V3.2"

    verification_sandbox_image: str = "devflow-verifier:py311"
    verification_sandbox_cpus: float = Field(default=1.0, ge=0.05, le=32.0)
    verification_sandbox_memory_mb: int = Field(default=512, ge=64, le=32_768)
    verification_sandbox_pids_limit: int = Field(default=128, ge=16, le=2_048)
    verification_sandbox_tmpfs_mb: int = Field(default=128, ge=16, le=4_096)
    verification_sandbox_shm_mb: int = Field(default=64, ge=16, le=1_024)
    verification_sandbox_timeout_seconds: float = Field(default=60.0, ge=0.05, le=600.0)

    git_clone_timeout_seconds: float = Field(default=300.0, gt=0.0, le=1_800.0)

    model_config = SettingsConfigDict(
        env_file=_REPOSITORY_ENV_FILE,
        env_file_encoding="utf-8",
        env_prefix="DEVFLOW_",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("workspace_root", mode="after")
    @classmethod
    def resolve_workspace_root(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (_REPOSITORY_ROOT / value).resolve()

    @field_validator(
        "siliconflow_api_key",
        "github_read_token",
        "github_publication_token",
        "github_token",
        mode="before",
    )
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("planner_model", "developer_model", "reviewer_model", "repair_model")
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent model ids must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_worker_lease_cadence(self) -> Self:
        if self.worker_heartbeat_interval_seconds >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat interval must be shorter than the lease duration")
        return self

    @property
    def effective_github_publication_token(self) -> SecretStr | None:
        return self.github_publication_token or self.github_token


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""

    return Settings()
