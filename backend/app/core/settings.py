from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse
from uuid import UUID

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.api.lifecycle_rollout import LifecycleRolloutMode
from app.models.work_package import WorkPackageActivationMode
from app.models.workflow import WorkflowActivationMode

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
    recovery_check_interval_seconds: float = Field(default=30.0, ge=5.0, le=3_600.0)
    recovery_startup_timeout_seconds: float = Field(default=120.0, ge=30.0, le=86_400.0)
    recovery_stale_progress_seconds: float = Field(default=180.0, ge=30.0, le=86_400.0)
    # Dramatiq's thread-interrupt timeout is an emergency circuit breaker, not an Agent budget.
    # It must leave enough room for a bounded Agent to return its structured result and for the
    # worker to persist terminal evidence before the process is interrupted.
    worker_task_time_limit_seconds: float = Field(default=7_200.0, gt=0.0, le=86_400.0)
    worker_terminal_persistence_grace_seconds: float = Field(
        default=180.0,
        ge=30.0,
        le=1_800.0,
    )
    continuation_max_slices: int = Field(default=5, ge=1, le=20)
    continuation_total_budget_seconds: float = Field(default=3_600.0, ge=600.0, le=86_400.0)
    continuation_max_repeated_file_slices: int = Field(default=1, ge=0, le=5)
    dag_max_concurrent_tasks: int = Field(default=2, ge=1, le=16)
    # Begin conservatively: deterministic Workflows are preferred only when their template is
    # fully derivable. Agent-only remains an immediate rollback switch.
    workflow_activation_mode: WorkflowActivationMode = WorkflowActivationMode.WORKFLOW_FIRST
    work_package_activation_mode: WorkPackageActivationMode = (
        WorkPackageActivationMode.WORK_PACKAGE_FIRST
    )
    # Context-economy rollout controls. They default on, but each can be disabled
    # independently for a safe rollback without changing execution authority.
    context_compaction_enabled: bool = True
    role_context_projection_enabled: bool = True
    # Tool result retention is a context-window control, not a financial budget.
    agent_max_single_tool_result_tokens: int = Field(default=1_200, ge=128, le=32_768)
    agent_max_tool_results_per_turn_tokens: int = Field(default=2_400, ge=128, le=65_536)
    adaptive_package_budget_enabled: bool = True
    adaptive_work_package_routing_enabled: bool = True
    # Lifecycle/recovery mutations can be enabled progressively without hiding their
    # diagnostics.  Start with test_database, then a test repository, then explicitly
    # listed project ids before using the default all-project behaviour.
    lifecycle_rollout_mode: LifecycleRolloutMode = LifecycleRolloutMode.DEFAULT
    lifecycle_test_repository_url: str | None = None
    lifecycle_project_allowlist: str = ""

    siliconflow_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DASHSCOPE_API_KEY",
            "GEMINI_API_KEY",
            "SILICONFLOW_API_KEY",
        ),
    )
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_timeout_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    siliconflow_max_retries: int = Field(default=0, ge=0, le=5)
    # Prefer an explicit model-provider proxy, while accepting the existing shared Clash proxy
    # variable for local development compatibility.
    siliconflow_proxy_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEVFLOW_SILICONFLOW_PROXY_URL", "DEVFLOW_PROXY_URL"),
    )

    # Bounded agent budgets are intentionally independent of the Worker lease heartbeat. They
    # prevent a slow provider turn from consuming a whole repair attempt or stopping it too early.
    developer_max_iterations: int = Field(default=12, ge=1, le=20)
    # Complex repository tasks commonly need an initial inspection turn before they can make any
    # change. Keep the Worker heartbeat independent, while allowing that bounded exploration.
    developer_max_duration_seconds: float = Field(default=600.0, ge=1.0, le=600.0)
    developer_max_model_turn_seconds: float = Field(default=180.0, ge=1.0, le=600.0)
    repair_max_iterations: int = Field(default=10, ge=1, le=20)
    repair_max_duration_seconds: float = Field(default=300.0, ge=1.0, le=600.0)
    repair_max_model_turn_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    minimum_repair_attempts: int = Field(default=2, ge=0, le=5)

    # Read and publication credentials are distinct capabilities. The legacy DEVFLOW_GITHUB_TOKEN
    # alias remains accepted for publication-only compatibility.
    github_read_token: SecretStr | None = None
    github_publication_token: SecretStr | None = None
    github_token: SecretStr | None = None
    # This local key is deliberately separate from GitHub and model-provider tokens. It is used
    # by PostgreSQL pgcrypto to encrypt project-scoped publication credentials at rest.
    secrets_encryption_key: SecretStr | None = None
    github_publication_timeout_seconds: float = Field(default=30.0, gt=0.0, le=30.0)

    # /readyz validates configured model ids against the provider catalogue instead of assuming
    # that a historical default remains available forever.
    planner_model: str = "zai-org/GLM-5.2"
    developer_model: str = "Pro/deepseek-ai/DeepSeek-V3.2"
    reviewer_model: str = "zai-org/GLM-5.2"
    repair_model: str = "Pro/deepseek-ai/DeepSeek-V3.2"
    failure_explanation_model: str = "zai-org/GLM-5.2"
    planner_enable_thinking: bool = False
    developer_enable_thinking: bool = False
    reviewer_enable_thinking: bool = False
    repair_enable_thinking: bool = False
    failure_explanation_enable_thinking: bool = False
    # Role-specific completion caps prevent auxiliary agents from consuming a coding-sized
    # response budget. They are intentionally separate from time and iteration limits.
    # Planning has one initial generation and at most one controlled recovery.  Separate caps
    # keep a JSON/schema repair from reserving a full first-plan completion budget.
    # The legacy key remains an alias for the initial cap so existing local .env files continue
    # to start safely during the rollout.
    planner_initial_max_output_tokens: int = Field(
        default=1_000,
        ge=64,
        le=32_768,
        validation_alias=AliasChoices(
            "DEVFLOW_PLANNER_INITIAL_MAX_OUTPUT_TOKENS",
            "DEVFLOW_PLANNER_MAX_OUTPUT_TOKENS",
        ),
    )
    planner_json_repair_max_output_tokens: int = Field(default=700, ge=64, le=32_768)
    planner_budget_replan_max_output_tokens: int = Field(default=800, ge=64, le=32_768)
    developer_max_output_tokens: int = Field(default=1_400, ge=64, le=32_768)
    # Used only for one diagnosed malformed write_file retry. The regular Developer cap remains
    # small; this controlled exception prevents a source-file JSON payload being truncated.
    developer_invalid_tool_retry_max_output_tokens: int = Field(
        default=3_200, ge=64, le=32_768
    )
    reviewer_max_output_tokens: int = Field(default=800, ge=64, le=32_768)
    repair_max_output_tokens: int = Field(default=1_000, ge=64, le=32_768)
    failure_explanation_max_output_tokens: int = Field(default=400, ge=64, le=32_768)
    run_token_budget_tokens: int = Field(default=50_000, ge=1_000, le=10_000_000)
    planner_token_budget_tokens: int = Field(default=7_200, ge=256, le=1_000_000)
    planner_max_attempts: int = Field(default=2, ge=1, le=4)
    token_estimate_safety_factor: float = Field(default=1.15, ge=1.0, le=2.0)

    verification_sandbox_image: str = "devflow-verifier:py311"
    verification_node_sandbox_image: str = "devflow-verifier:node24"
    verification_sandbox_cpus: float = Field(default=1.0, ge=0.05, le=32.0)
    verification_sandbox_memory_mb: int = Field(default=512, ge=64, le=32_768)
    verification_sandbox_pids_limit: int = Field(default=128, ge=16, le=2_048)
    verification_sandbox_tmpfs_mb: int = Field(default=128, ge=16, le=4_096)
    verification_sandbox_shm_mb: int = Field(default=64, ge=16, le=1_024)
    verification_sandbox_timeout_seconds: float = Field(default=60.0, ge=0.05, le=600.0)
    # Dependency preparation happens before an Agent Run is persisted or dispatched. The proxy
    # is used only by the trusted preflight/builder process, never injected into verification
    # containers or Agent tool calls.
    dependency_proxy_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEVFLOW_DEPENDENCY_PROXY_URL", "DEVFLOW_PROXY_URL"),
    )
    dependency_python_index_url: str = "https://pypi.org/simple/"
    dependency_node_registry_url: str = "https://registry.npmjs.org/"
    dependency_preflight_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    dependency_preflight_build_timeout_seconds: float = Field(default=60.0, ge=10.0, le=600.0)
    dependency_cache_max_bytes: int = Field(default=5 * 1024 * 1024 * 1024, ge=64 * 1024 * 1024)

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
        "secrets_encryption_key",
        mode="before",
    )
    @classmethod
    def normalize_optional_secret(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "planner_model",
        "developer_model",
        "reviewer_model",
        "repair_model",
        "failure_explanation_model",
    )
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Agent model ids must not be empty")
        return normalized

    @field_validator("dependency_proxy_url", "siliconflow_proxy_url", mode="before")
    @classmethod
    def normalize_proxy_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("dependency proxy URL must be an absolute http:// or https:// URL")
        return normalized

    @field_validator("dependency_python_index_url", "dependency_node_registry_url")
    @classmethod
    def normalize_dependency_registry_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("dependency registry URL must be an absolute http:// or https:// URL")
        return normalized + "/"

    @model_validator(mode="after")
    def validate_worker_lease_cadence(self) -> Self:
        if self.worker_heartbeat_interval_seconds >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat interval must be shorter than the lease duration")
        # A TaskContract permits up to five repair attempts. The outer Dramatiq limit must
        # cover the entire bounded lifecycle, not merely Developer execution, otherwise it can
        # interrupt the task before terminal evidence is persisted.
        single_slice_lifecycle = (
            self.developer_max_duration_seconds
            + (5 * self.repair_max_duration_seconds)
            + (6 * self.verification_sandbox_timeout_seconds)
            + self.siliconflow_timeout_seconds
            + self.worker_terminal_persistence_grace_seconds
        )
        maximum_task_lifecycle = max(
            single_slice_lifecycle,
            self.continuation_total_budget_seconds + self.worker_terminal_persistence_grace_seconds,
        )
        if self.worker_task_time_limit_seconds <= maximum_task_lifecycle:
            raise ValueError(
                "worker task time limit must exceed the maximum bounded task lifecycle "
                "including repair, verification, review, and terminal persistence"
            )
        if self.developer_max_model_turn_seconds > self.developer_max_duration_seconds:
            raise ValueError("developer model turn limit must not exceed developer duration")
        if self.repair_max_model_turn_seconds > self.repair_max_duration_seconds:
            raise ValueError("repair model turn limit must not exceed repair duration")
        if self.agent_max_tool_results_per_turn_tokens < self.agent_max_single_tool_result_tokens:
            raise ValueError(
                "per-turn tool-result context budget must cover one complete tool result"
            )
        return self

    @property
    def effective_github_publication_token(self) -> SecretStr | None:
        return self.github_publication_token or self.github_token

    @property
    def lifecycle_project_ids(self) -> frozenset[UUID]:
        values = [value.strip() for value in self.lifecycle_project_allowlist.split(",")]
        try:
            return frozenset(UUID(value) for value in values if value)
        except ValueError as exc:
            raise ValueError(
                "DEVFLOW_LIFECYCLE_PROJECT_ALLOWLIST 必须是逗号分隔的项目 UUID"
            ) from exc


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""

    return Settings()
