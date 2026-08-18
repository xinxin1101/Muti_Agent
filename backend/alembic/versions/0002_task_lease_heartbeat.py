"""add task execution lease and heartbeat state

Revision ID: 0002_task_lease_heartbeat
Revises: 0001_postgresql_evidence
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_task_lease_heartbeat"
down_revision = "0001_postgresql_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("lease_owner", sa.String(length=255), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("lease_dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("lease_acquired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("lease_released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_tasks_lease_shape",
        "tasks",
        "(" 
        "lease_owner IS NULL AND lease_dispatch_id IS NULL "
        "AND lease_acquired_at IS NULL AND heartbeat_at IS NULL "
        "AND lease_until IS NULL AND lease_released_at IS NULL"
        ") OR ("
        "lease_owner IS NOT NULL AND lease_dispatch_id IS NOT NULL "
        "AND lease_acquired_at IS NOT NULL AND heartbeat_at IS NOT NULL "
        "AND lease_until IS NOT NULL"
        ")",
    )
    op.create_index("ix_tasks_lease_until", "tasks", ["lease_until"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_lease_until", table_name="tasks")
    op.drop_constraint("ck_tasks_lease_shape", "tasks", type_="check")
    op.drop_column("tasks", "lease_released_at")
    op.drop_column("tasks", "lease_until")
    op.drop_column("tasks", "heartbeat_at")
    op.drop_column("tasks", "lease_acquired_at")
    op.drop_column("tasks", "lease_dispatch_id")
    op.drop_column("tasks", "lease_owner")
