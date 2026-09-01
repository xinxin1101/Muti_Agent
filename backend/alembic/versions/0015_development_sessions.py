"""add durable development sessions for planning and Run continuation

Revision ID: 0015_development_sessions
Revises: 0014_flex_work_package_budgets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_development_sessions"
down_revision: str | None = "0014_flex_work_package_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "development_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requirement", sa.Text(), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("repository_context_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("dag_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dag_sha256", sa.String(length=64), nullable=True),
        sa.Column("planning_diagnostic", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("planning_launch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("latest_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resumed_from_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "state IN ('PLANNING','PAUSED_PLANNING','PLANNING_FAILED',"
            "'READY_TO_RUN','RUNNING','COMPLETED')",
            name="ck_development_sessions_state",
        ),
        sa.CheckConstraint(
            "(dag_payload IS NULL AND dag_sha256 IS NULL) OR "
            "(dag_payload IS NOT NULL AND dag_sha256 IS NOT NULL)",
            name="ck_development_sessions_dag_shape",
        ),
    )
    op.create_index(
        "ix_development_sessions_project_updated",
        "development_sessions",
        ["project_id", "updated_at"],
    )
    op.create_table(
        "development_session_work_packages",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column(
            "completed_interfaces",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("verification_summary", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("failure_summary", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("remaining_budget_tokens", sa.Integer(), nullable=True),
        sa.Column("context_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["session_id"], ["development_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "task_id"),
        sa.CheckConstraint(
            "state IN ('PENDING','RUNNING','SUCCEEDED','CHECKPOINTED','FAILED','BLOCKED')",
            name="ck_development_session_work_packages_state",
        ),
        sa.CheckConstraint(
            "remaining_budget_tokens IS NULL OR remaining_budget_tokens >= 0",
            name="ck_development_session_work_packages_budget",
        ),
    )
    op.add_column(
        "runs", sa.Column("development_session_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "runs", sa.Column("resumed_from_run_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_runs_development_session",
        "runs",
        "development_sessions",
        ["development_session_id"],
        ["id"],
    )
    op.create_foreign_key("fk_runs_resumed_from", "runs", "runs", ["resumed_from_run_id"], ["id"])
    op.create_index("ix_runs_development_session", "runs", ["development_session_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_development_session", table_name="runs")
    op.drop_constraint("fk_runs_resumed_from", "runs", type_="foreignkey")
    op.drop_constraint("fk_runs_development_session", "runs", type_="foreignkey")
    op.drop_column("runs", "resumed_from_run_id")
    op.drop_column("runs", "development_session_id")
    op.drop_table("development_session_work_packages")
    op.drop_index("ix_development_sessions_project_updated", table_name="development_sessions")
    op.drop_table("development_sessions")
