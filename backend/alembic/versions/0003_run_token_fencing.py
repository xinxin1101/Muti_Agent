"""add run token fencing generation

Revision ID: 0003_run_token_fencing
Revises: 0002_task_lease_heartbeat
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_run_token_fencing"
down_revision = "0002_task_lease_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_tasks_lease_shape", "tasks", type_="check")
    op.add_column(
        "tasks",
        sa.Column(
            "lease_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("run_token", postgresql.UUID(as_uuid=True), nullable=True),
    )

    # Existing Step 3.6-owned rows need an immediately fenced generation on upgrade. The old
    # worker never received this migration token, so any post-upgrade write without it fails closed.
    op.execute(
        sa.text(
            "UPDATE tasks "
            "SET lease_generation = 1, run_token = gen_random_uuid() "
            "WHERE lease_owner IS NOT NULL"
        )
    )

    op.create_unique_constraint("uq_tasks_run_token", "tasks", ["run_token"])
    op.create_check_constraint(
        "ck_tasks_lease_shape",
        "tasks",
        "("
        "lease_owner IS NULL AND lease_dispatch_id IS NULL "
        "AND lease_acquired_at IS NULL AND heartbeat_at IS NULL "
        "AND lease_until IS NULL AND lease_released_at IS NULL "
        "AND run_token IS NULL AND lease_generation = 0"
        ") OR ("
        "lease_owner IS NOT NULL AND lease_dispatch_id IS NOT NULL "
        "AND lease_acquired_at IS NOT NULL AND heartbeat_at IS NOT NULL "
        "AND lease_until IS NOT NULL AND run_token IS NOT NULL "
        "AND lease_generation >= 1"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_tasks_lease_shape", "tasks", type_="check")
    op.drop_constraint("uq_tasks_run_token", "tasks", type_="unique")
    op.drop_column("tasks", "run_token")
    op.drop_column("tasks", "lease_generation")
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
