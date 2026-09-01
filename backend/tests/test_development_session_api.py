from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.autonomous import (
    InitialTaskDispatch,
    RequirementDispatchState,
    RequirementRunLaunchResponse,
    RequirementRunLaunchState,
    attach_autonomous_routes,
)
from app.api.models import (
    ProductDevelopmentSession,
    ProductDevelopmentSessionCommandPreview,
    ProductDevelopmentSessionRecovery,
    ProductDevelopmentSessionRecoveryBudget,
    ProductDevelopmentSessionTimelineEntry,
)
from app.models.development_session import (
    DevelopmentSessionBaselineState,
    DevelopmentSessionCommandIntent,
    DevelopmentSessionContinuationMode,
    DevelopmentSessionState,
    DevelopmentSessionTimelineKind,
)


class _SessionService:
    def __init__(self) -> None:
        self.continuation_mode: DevelopmentSessionContinuationMode | None = None
        self.previewed_command: str | None = None

    @staticmethod
    def _session(session_id: UUID) -> ProductDevelopmentSession:
        return ProductDevelopmentSession(
            session_id=session_id,
            project_id=UUID("11111111-1111-1111-1111-111111111111"),
            requirement="继续开发网页游戏",
            base_commit="a" * 40,
            state=DevelopmentSessionState.RUNNING,
            planning_diagnostic="",
            latest_run_id=UUID("22222222-2222-2222-2222-222222222222"),
            work_packages=(),
            created_at="2026-08-31T00:00:00Z",
            updated_at="2026-08-31T00:00:00Z",
        )

    async def list_project_development_sessions(self, project_id: UUID):
        return (self._session(UUID("33333333-3333-3333-3333-333333333333")),)

    async def get_development_session_timeline(self, session_id: UUID):
        return (
            ProductDevelopmentSessionTimelineEntry(
                entry_id=1,
                session_id=session_id,
                kind=DevelopmentSessionTimelineKind.USER_REQUIREMENT,
                title="用户提出开发需求",
                detail="需求已保存。",
                created_at="2026-08-31T00:00:00Z",
            ),
        )

    async def preview_development_session_command(self, session_id: UUID, request):
        self.previewed_command = request.command
        return ProductDevelopmentSessionCommandPreview(
            session_id=session_id,
            intent=DevelopmentSessionCommandIntent.DELETE_PROJECT,
            action_name="永久删除项目本地数据",
            target_label="test",
            impact=("GitHub 仓库不会被删除。",),
            token_cost="不消耗模型 Token",
            affects_local_data=True,
            executable_after_confirmation=True,
            confirmation_hint="下一步仍需二次确认。",
        )

    async def continue_development_session(self, session_id: UUID, *, mode):
        self.continuation_mode = mode
        return RequirementRunLaunchResponse(
            run_id=UUID("22222222-2222-2222-2222-222222222222"),
            project_id=UUID("11111111-1111-1111-1111-111111111111"),
            base_commit="a" * 40,
            dag_sha256="b" * 64,
            task_ids=("web",),
            initial_ready_task_ids=("web",),
            launch_state=RequirementRunLaunchState.QUEUED,
            dispatches=(
                InitialTaskDispatch(task_id="web", state=RequirementDispatchState.QUEUED),
            ),
        )

    async def get_development_session_recovery(self, session_id: UUID):
        return ProductDevelopmentSessionRecovery(
            session_id=session_id,
            source_run_id=None,
            baseline_commit="a" * 40,
            current_commit="b" * 40,
            baseline_state=DevelopmentSessionBaselineState.CHANGED,
            reusable_work_package_ids=("core",),
            checkpointed_work_package_ids=(),
            remaining_work_package_ids=("web",),
            next_action="重新规划或明确基于旧基线继续",
            budget=ProductDevelopmentSessionRecoveryBudget(
                planning_remaining_tokens=4000,
                development_remaining_tokens=6000,
                repair_remaining_tokens=2000,
                estimated_new_development_tokens=5000,
                estimated_tokens_saved=6000,
            ),
        )


def test_recovery_preview_reports_baseline_change_and_continue_requires_explicit_mode() -> None:
    service = _SessionService()
    app = FastAPI()
    attach_autonomous_routes(app, service)  # type: ignore[arg-type]
    session_id = "33333333-3333-3333-3333-333333333333"
    client = TestClient(app)

    preview = client.get(f"/api/v1/development-sessions/{session_id}/recovery-preview")
    assert preview.status_code == 200
    assert preview.json()["baseline_state"] == "CHANGED"
    assert preview.json()["reusable_work_package_ids"] == ["core"]

    response = client.post(
        f"/api/v1/development-sessions/{session_id}/continue?mode=OLD_BASE"
    )
    assert response.status_code == 201
    assert service.continuation_mode is DevelopmentSessionContinuationMode.OLD_BASE


def test_session_timeline_and_command_preview_are_read_only_until_confirmation() -> None:
    service = _SessionService()
    app = FastAPI()
    attach_autonomous_routes(app, service)  # type: ignore[arg-type]
    client = TestClient(app)
    session_id = "33333333-3333-3333-3333-333333333333"

    sessions = client.get(
        "/api/v1/projects/11111111-1111-1111-1111-111111111111/development-sessions"
    )
    timeline = client.get(f"/api/v1/development-sessions/{session_id}/timeline")
    preview = client.post(
        f"/api/v1/development-sessions/{session_id}/command-preview",
        json={"command": "删除 test 项目"},
    )

    assert sessions.status_code == 200
    assert sessions.json()[0]["session_id"] == session_id
    assert timeline.status_code == 200
    assert timeline.json()[0]["kind"] == "USER_REQUIREMENT"
    assert preview.status_code == 200
    assert preview.json()["intent"] == "DELETE_PROJECT"
    assert service.previewed_command == "删除 test 项目"
    assert service.continuation_mode is None


def test_requirement_creation_persistence_error_keeps_cors_and_hides_database_detail() -> None:
    class _FailingService:
        async def create_requirement_run(self, _request):  # type: ignore[no-untyped-def]
            raise SQLAlchemyError("database implementation detail")

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    attach_autonomous_routes(app, _FailingService())  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        "/api/v1/runs/from-requirement",
        headers={"Origin": "http://127.0.0.1:5173"},
        json={
            "project_id": "11111111-1111-1111-1111-111111111111",
            "requirement": "实现网页五子棋",
        },
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert response.json()["detail"] == "创建开发会话的本地持久化失败，请查看后端日志。"


def test_requirement_creation_validation_error_is_not_mapped_to_not_found() -> None:
    class _InvalidService:
        async def create_requirement_run(self, _request):  # type: ignore[no-untyped-def]
            raise ValueError("not enough values to unpack (expected 3, got 2)")

    app = FastAPI()
    attach_autonomous_routes(app, _InvalidService())  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.post(
        "/api/v1/runs/from-requirement",
        json={
            "project_id": "11111111-1111-1111-1111-111111111111",
            "requirement": "实现网页五子棋",
        },
    )

    assert response.status_code == 422
