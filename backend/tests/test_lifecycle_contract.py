from app.models.lifecycle import (
    DEVELOPMENT_SESSION_LIFECYCLE_TRANSITIONS,
    PROJECT_LIFECYCLE_TRANSITIONS,
    RUN_LIFECYCLE_TRANSITIONS,
    DevelopmentSessionLifecycleState,
    ProjectLifecycleState,
    RunLifecycleState,
    transition_is_allowed,
)


def test_project_deletion_requires_the_deleting_state() -> None:
    assert transition_is_allowed(
        ProjectLifecycleState.ACTIVE,
        ProjectLifecycleState.ARCHIVED,
        PROJECT_LIFECYCLE_TRANSITIONS,
    )
    assert not transition_is_allowed(
        ProjectLifecycleState.ACTIVE,
        ProjectLifecycleState.DELETED,
        PROJECT_LIFECYCLE_TRANSITIONS,
    )


def test_finished_run_cannot_be_reopened() -> None:
    assert not transition_is_allowed(
        RunLifecycleState.FAILED,
        RunLifecycleState.RUNNING,
        RUN_LIFECYCLE_TRANSITIONS,
    )


def test_development_session_can_pause_then_resume() -> None:
    assert transition_is_allowed(
        DevelopmentSessionLifecycleState.RUNNING,
        DevelopmentSessionLifecycleState.PAUSED_PLANNING,
        DEVELOPMENT_SESSION_LIFECYCLE_TRANSITIONS,
    )
