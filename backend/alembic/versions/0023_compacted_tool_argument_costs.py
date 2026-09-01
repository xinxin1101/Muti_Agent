"""record tool arguments omitted by deterministic context compaction

Revision ID: 0023_compacted_tool_args
Revises: 0022_cost_predictions
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_compacted_tool_args"
down_revision: str | None = "0022_cost_predictions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_task_cost_observations",
        sa.Column(
            "compacted_tool_argument_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(
        "ck_run_task_cost_observation_compacted_args_nonnegative",
        "run_task_cost_observations",
        "compacted_tool_argument_tokens >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_run_task_cost_observation_compacted_args_nonnegative",
        "run_task_cost_observations",
        type_="check",
    )
    op.drop_column("run_task_cost_observations", "compacted_tool_argument_tokens")
