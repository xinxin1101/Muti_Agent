"""Explicit, fail-closed rollout guard for lifecycle and recovery mutations.

The guard deliberately governs only local DevFlow management actions.  It never
changes GitHub authority and keeps read-only diagnostics available while a
feature is being trialled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.api.models import ProductProject


class LifecycleRolloutMode(StrEnum):
    """Progressive enablement stages for destructive/recovery controls."""

    TEST_DATABASE = "test_database"
    TEST_REPOSITORY = "test_repository"
    PROJECT_ALLOWLIST = "project_allowlist"
    DEFAULT = "default"


@dataclass(frozen=True)
class LifecycleRolloutGate:
    """Authorize a local lifecycle mutation only in the configured rollout scope."""

    mode: LifecycleRolloutMode = LifecycleRolloutMode.DEFAULT
    environment: str = "development"
    test_repository_url: str | None = None
    project_allowlist: frozenset[UUID] = frozenset()

    def is_enabled(self, project: ProductProject) -> bool:
        if self.mode is LifecycleRolloutMode.DEFAULT:
            return True
        if self.mode is LifecycleRolloutMode.TEST_DATABASE:
            return self.environment == "test"
        if self.mode is LifecycleRolloutMode.TEST_REPOSITORY:
            return bool(self.test_repository_url) and _canonical_url(
                project.repository_url
            ) == _canonical_url(self.test_repository_url)
        return project.project_id in self.project_allowlist

    def disabled_reason(self, project: ProductProject) -> str:
        if self.mode is LifecycleRolloutMode.TEST_DATABASE:
            return "项目/运行生命周期操作当前仅在测试数据库启用。"
        if self.mode is LifecycleRolloutMode.TEST_REPOSITORY:
            return "项目/运行生命周期操作当前仅对配置的测试仓库启用。"
        if self.mode is LifecycleRolloutMode.PROJECT_ALLOWLIST:
            return "项目/运行生命周期操作当前仅对配置的测试项目启用。"
        return f"项目/运行生命周期操作未对项目 {project.project_id} 启用。"


def _canonical_url(value: str) -> str:
    return value.strip().rstrip("/").removesuffix(".git").lower()
