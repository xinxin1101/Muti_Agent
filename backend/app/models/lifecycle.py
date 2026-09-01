from __future__ import annotations

from enum import StrEnum


class ProjectLifecycleState(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DELETING = "DELETING"
    DELETED = "DELETED"


class RunLifecycleState(StrEnum):
    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class RunVisibilityState(StrEnum):
    VISIBLE = "VISIBLE"
    ARCHIVED = "ARCHIVED"


class RunDisplayStatus(StrEnum):
    """User-facing liveness projection; execution status remains immutable evidence."""

    RUNNING = "RUNNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    FAILED = "FAILED"
    SUCCEEDED = "SUCCEEDED"


class DevelopmentSessionLifecycleState(StrEnum):
    PLANNING = "PLANNING"
    PAUSED_PLANNING = "PAUSED_PLANNING"
    PLANNING_FAILED = "PLANNING_FAILED"
    READY_TO_RUN = "READY_TO_RUN"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"


PROJECT_LIFECYCLE_TRANSITIONS = {
    ProjectLifecycleState.ACTIVE: frozenset(
        {ProjectLifecycleState.ARCHIVED, ProjectLifecycleState.DELETING}
    ),
    ProjectLifecycleState.ARCHIVED: frozenset(
        {ProjectLifecycleState.ACTIVE, ProjectLifecycleState.DELETING}
    ),
    ProjectLifecycleState.DELETING: frozenset({ProjectLifecycleState.DELETED}),
    ProjectLifecycleState.DELETED: frozenset(),
}

RUN_LIFECYCLE_TRANSITIONS = {
    RunLifecycleState.RUNNING: frozenset(
        {
            RunLifecycleState.WAITING_EXTERNAL,
            RunLifecycleState.RECOVERY_REQUIRED,
            RunLifecycleState.SUCCEEDED,
            RunLifecycleState.FAILED,
        }
    ),
    RunLifecycleState.WAITING_EXTERNAL: frozenset(
        {
            RunLifecycleState.RUNNING,
            RunLifecycleState.RECOVERY_REQUIRED,
            RunLifecycleState.FAILED,
        }
    ),
    RunLifecycleState.RECOVERY_REQUIRED: frozenset({RunLifecycleState.ARCHIVED}),
    RunLifecycleState.SUCCEEDED: frozenset({RunLifecycleState.ARCHIVED}),
    RunLifecycleState.FAILED: frozenset({RunLifecycleState.ARCHIVED}),
    RunLifecycleState.ARCHIVED: frozenset(),
}

DEVELOPMENT_SESSION_LIFECYCLE_TRANSITIONS = {
    DevelopmentSessionLifecycleState.PLANNING: frozenset(
        {
            DevelopmentSessionLifecycleState.PAUSED_PLANNING,
            DevelopmentSessionLifecycleState.PLANNING_FAILED,
            DevelopmentSessionLifecycleState.READY_TO_RUN,
        }
    ),
    DevelopmentSessionLifecycleState.PAUSED_PLANNING: frozenset(
        {
            DevelopmentSessionLifecycleState.READY_TO_RUN,
            DevelopmentSessionLifecycleState.PLANNING_FAILED,
        }
    ),
    DevelopmentSessionLifecycleState.PLANNING_FAILED: frozenset(),
    DevelopmentSessionLifecycleState.READY_TO_RUN: frozenset(
        {DevelopmentSessionLifecycleState.RUNNING}
    ),
    DevelopmentSessionLifecycleState.RUNNING: frozenset(
        {
            DevelopmentSessionLifecycleState.PAUSED_PLANNING,
            DevelopmentSessionLifecycleState.COMPLETED,
        }
    ),
    DevelopmentSessionLifecycleState.COMPLETED: frozenset(),
}


def transition_is_allowed(current: StrEnum, target: StrEnum, transitions: dict) -> bool:
    """Pure contract used by later archive/delete/recovery command handlers."""

    return target in transitions[current]
