import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.api.hardened import HardenedOperatorAwareAutonomousProductRuntimeService
from app.api.lifecycle_rollout import LifecycleRolloutGate, LifecycleRolloutMode
from app.api.models import ProductProject
from app.models.lifecycle import ProjectLifecycleState
from app.persistence.errors import PersistenceConflictError


def _project(*, repository_url: str = "https://github.com/example/test") -> ProductProject:
    return ProductProject(
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        repository_url=repository_url,
        default_branch="main",
        created_at=datetime(2026, 8, 31, tzinfo=UTC),
        run_count=0,
        workspace_ready=True,
        provision_status="READY",
        provision_error_code=None,
        provision_error_message=None,
        lifecycle_state=ProjectLifecycleState.ACTIVE,
    )


def test_rollout_sequence_is_scoped_before_default_enablement() -> None:
    project = _project()

    assert not LifecycleRolloutGate(
        mode=LifecycleRolloutMode.TEST_DATABASE, environment="development"
    ).is_enabled(project)
    assert LifecycleRolloutGate(
        mode=LifecycleRolloutMode.TEST_DATABASE, environment="test"
    ).is_enabled(project)
    assert LifecycleRolloutGate(
        mode=LifecycleRolloutMode.TEST_REPOSITORY,
        test_repository_url="https://github.com/example/test.git",
    ).is_enabled(project)
    assert not LifecycleRolloutGate(
        mode=LifecycleRolloutMode.PROJECT_ALLOWLIST,
        project_allowlist=frozenset({uuid4()}),
    ).is_enabled(project)
    assert LifecycleRolloutGate(
        mode=LifecycleRolloutMode.PROJECT_ALLOWLIST,
        project_allowlist=frozenset({project.project_id}),
    ).is_enabled(project)
    assert LifecycleRolloutGate().is_enabled(project)


def test_rollout_denial_has_a_user_safe_reason() -> None:
    gate = LifecycleRolloutGate(mode=LifecycleRolloutMode.PROJECT_ALLOWLIST)

    assert "测试项目" in gate.disabled_reason(_project())


def test_hardened_service_fails_closed_before_a_lifecycle_mutation() -> None:
    service = object.__new__(HardenedOperatorAwareAutonomousProductRuntimeService)
    service._lifecycle_rollout_gate = LifecycleRolloutGate(  # type: ignore[attr-defined]
        mode=LifecycleRolloutMode.PROJECT_ALLOWLIST
    )

    async def get_project(_project_id: UUID) -> ProductProject:
        return _project()

    service.get_project = get_project  # type: ignore[method-assign]
    with pytest.raises(PersistenceConflictError, match="测试项目"):
        asyncio.run(service._ensure_lifecycle_rollout_enabled(uuid4()))
