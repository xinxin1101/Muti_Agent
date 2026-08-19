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
    op.add_column(
        "tasks",
        sa.Column(
            "depends_on",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "depends_on")
