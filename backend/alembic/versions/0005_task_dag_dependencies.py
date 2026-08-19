"""persist validated task DAG dependencies

Revision ID: 0005_task_dag_dependencies
Revises: 0004_structured_runtime_events
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_task_dag_dependencies"
down_revision = "0004_structured_runtime_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL deliberately means "authoritative topology was never persisted".
    # Step 4.4 must not rewrite historical multi-task runs as independent tasks.
    op.add_column(
        "tasks",
        sa.Column(
            "depends_on",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "runs",
        sa.Column("dag_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "dag_sha256")
    op.drop_column("tasks", "depends_on")
