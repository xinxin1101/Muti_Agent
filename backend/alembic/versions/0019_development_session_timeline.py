"""add bounded development-session conversation timeline

Revision ID: 0019_session_timeline
Revises: 0018_run_recovery_state
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019_session_timeline"
down_revision: str | None = "0018_run_recovery_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "development_session_timeline_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("detail", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["session_id"], ["development_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("session_id", "event_key", name="uq_development_session_timeline_key"),
        sa.CheckConstraint(
            "kind IN ('USER_REQUIREMENT','PLAN_DRAFT','BUDGET_DIAGNOSTIC',"
            "'WORK_PACKAGE_SUCCEEDED','WORK_PACKAGE_FAILED','WORK_PACKAGE_CHECKPOINTED',"
            "'RECOVERY_PREVIEW','USER_ACTION','RUN_LINKED')",
            name="ck_development_session_timeline_kind",
        ),
    )
    op.create_index(
        "ix_development_session_timeline_session_created",
        "development_session_timeline_entries",
        ["session_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_development_session_timeline_session_created",
        table_name="development_session_timeline_entries",
    )
    op.drop_table("development_session_timeline_entries")
