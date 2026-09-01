"""add project lifecycle and non-authorizing run visibility

Revision ID: 0017_project_run_lifecycle
Revises: 0016_operation_audits
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_project_run_lifecycle"
down_revision: str | None = "0016_operation_audits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False, server_default="ACTIVE"),
    )
    op.create_check_constraint(
        "ck_projects_lifecycle_state",
        "projects",
        "lifecycle_state IN ('ACTIVE','ARCHIVED','DELETING','DELETED')",
    )
    op.create_index("ix_projects_lifecycle_created", "projects", ["lifecycle_state", "created_at"])
    op.add_column(
        "runs",
        sa.Column(
            "visibility_status", sa.String(length=16), nullable=False, server_default="VISIBLE"
        ),
    )
    op.create_check_constraint(
        "ck_runs_visibility_status",
        "runs",
        "visibility_status IN ('VISIBLE','ARCHIVED')",
    )
    op.create_index("ix_runs_visibility_started", "runs", ["visibility_status", "started_at"])

    # An audit must survive local-project deletion.  Its UUID references are historical labels,
    # not live foreign-key ownership links.
    op.execute(
        "ALTER TABLE operation_audits DROP CONSTRAINT IF EXISTS operation_audits_project_id_fkey"
    )
    op.execute(
        "ALTER TABLE operation_audits DROP CONSTRAINT IF EXISTS operation_audits_run_id_fkey"
    )
    op.execute(
        "ALTER TABLE operation_audits "
        "DROP CONSTRAINT IF EXISTS operation_audits_development_session_id_fkey"
    )
    op.drop_constraint("ck_operation_audits_action", "operation_audits", type_="check")
    op.create_check_constraint(
        "ck_operation_audits_action",
        "operation_audits",
        "action IN ('PROJECT_ARCHIVED','PROJECT_RESTORED','PROJECT_DELETED','RUN_ARCHIVED',"
        "'RUN_RECOVERED','DEVELOPMENT_SESSION_CONTINUED','DEVELOPMENT_SESSION_REPLANNED')",
    )


def downgrade() -> None:
    op.drop_index("ix_runs_visibility_started", table_name="runs")
    op.drop_constraint("ck_runs_visibility_status", "runs", type_="check")
    op.drop_column("runs", "visibility_status")
    op.drop_index("ix_projects_lifecycle_created", table_name="projects")
    op.drop_constraint("ck_projects_lifecycle_state", "projects", type_="check")
    op.drop_column("projects", "lifecycle_state")
