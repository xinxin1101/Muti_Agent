"""persist observed prompt sizes for work-package budget calibration

Revision ID: 0020_prompt_calibration
Revises: 0019_session_timeline
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_prompt_calibration"
down_revision: str | None = "0019_session_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "developer_observed_prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "repair_observed_prompt_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("run_task_token_budgets", "repair_observed_prompt_tokens")
    op.drop_column("run_task_token_budgets", "developer_observed_prompt_tokens")
