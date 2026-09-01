"""add append-only project and recovery operation audits

Revision ID: 0016_operation_audits
Revises: 0015_development_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_operation_audits"
down_revision: str | None = "0015_development_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_key", sa.String(length=255), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("development_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "impact_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("result_summary", sa.String(length=512), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["development_session_id"], ["development_sessions.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("operation_key", name="uq_operation_audits_operation_key"),
        sa.CheckConstraint(
            "action IN ('PROJECT_ARCHIVED','PROJECT_DELETED','RUN_ARCHIVED','RUN_RECOVERED',"
            "'DEVELOPMENT_SESSION_CONTINUED','DEVELOPMENT_SESSION_REPLANNED')",
            name="ck_operation_audits_action",
        ),
        sa.CheckConstraint(
            "outcome IN ('SUCCEEDED','REJECTED','FAILED')",
            name="ck_operation_audits_outcome",
        ),
        sa.CheckConstraint(
            "project_id IS NOT NULL OR run_id IS NOT NULL OR development_session_id IS NOT NULL",
            name="ck_operation_audits_target",
        ),
    )
    op.create_index("ix_operation_audits_created", "operation_audits", ["created_at"])
    op.create_index("ix_operation_audits_project", "operation_audits", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_operation_audits_project", table_name="operation_audits")
    op.drop_index("ix_operation_audits_created", table_name="operation_audits")
    op.drop_table("operation_audits")
