from datetime import UTC, datetime
from uuid import UUID

from app.api.models import ProductDevelopmentSession, ProductProject
from app.api.session_commands import DevelopmentSessionCommandPreviewer
from app.models.development_session import DevelopmentSessionCommandIntent, DevelopmentSessionState


def _session() -> ProductDevelopmentSession:
    return ProductDevelopmentSession(
        session_id=UUID("33333333-3333-3333-3333-333333333333"),
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        requirement="实现网页游戏",
        base_commit="a" * 40,
        state=DevelopmentSessionState.RUNNING,
        planning_diagnostic="",
        latest_run_id=UUID("22222222-2222-2222-2222-222222222222"),
        work_packages=(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _project() -> ProductProject:
    return ProductProject(
        project_id=UUID("11111111-1111-1111-1111-111111111111"),
        repository_url="https://github.com/example/test",
        default_branch="main",
        created_at=datetime.now(UTC),
        run_count=1,
        workspace_ready=True,
    )


def test_limited_conversation_commands_return_confirmation_cards_without_model_cost() -> None:
    preview = DevelopmentSessionCommandPreviewer().preview(
        session=_session(), project=_project(), command="基于旧基线继续开发"
    )

    assert preview.intent is DevelopmentSessionCommandIntent.CONTINUE_OLD_BASE
    assert preview.confirmation_required is True
    assert preview.token_cost == "将消耗后续开发/修复预算"
    assert preview.affects_local_data is False


def test_sensitive_or_unrecognized_command_is_not_executable_and_does_not_echo_secret() -> None:
    preview = DevelopmentSessionCommandPreviewer().preview(
        session=_session(), project=_project(), command="删除项目 token=sk-secret-value"
    )

    assert preview.intent is DevelopmentSessionCommandIntent.UNKNOWN
    assert preview.executable_after_confirmation is False
    assert "sk-secret" not in preview.model_dump_json()
