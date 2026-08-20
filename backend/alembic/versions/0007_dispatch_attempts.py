"""add durable dispatch attempt ledger

Revision ID: 0007_dispatch_attempts
Revises: 0006_github_publications
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_dispatch_attempts"
down_revision: str | None = "0006_github_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dispatch_attempts",
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("broker_message_id", sa.String(length=128), nullable=True),
        sa.Column("queue_name", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=512), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_dispatch_attempts_attempt_number",
        ),
        sa.CheckConstraint(
            "state IN ('REQUESTED', 'ENQUEUED', 'PUBLISH_FAILED')",
            name="ck_dispatch_attempts_state",
        ),
        sa.CheckConstraint(
            "(state = 'REQUESTED' AND broker_message_id IS NULL AND queue_name IS NULL "
            "AND error_code IS NULL AND error_message IS NULL AND resolved_at IS NULL) OR "
            "(state = 'ENQUEUED' AND broker_message_id IS NOT NULL AND queue_name IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL AND resolved_at IS NOT NULL) OR "
            "(state = 'PUBLISH_FAILED' AND broker_message_id IS NULL AND queue_name IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_dispatch_attempts_state_shape",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_dispatch_attempts_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("dispatch_id", name="pk_dispatch_attempts"),
        sa.UniqueConstraint(
            "run_id",
            "task_id",
            "attempt_number",
            name="uq_dispatch_attempts_task_number",
        ),
    )
    op.create_index(
        "ix_dispatch_attempts_run_task",
        "dispatch_attempts",
        ["run_id", "task_id", "attempt_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dispatch_attempts_run_task", table_name="dispatch_attempts")
    op.drop_table("dispatch_attempts")
