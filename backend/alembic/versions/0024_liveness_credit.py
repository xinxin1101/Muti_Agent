"""persist bounded work-package liveness-credit facts

Revision ID: 0024_liveness_credit
Revises: 0023_compacted_tool_args
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_liveness_credit"
down_revision: str | None = "0023_compacted_tool_args"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "tool_recovery_credit_used",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "last_liveness_credit",
            sa.String(length=32),
            nullable=False,
            server_default="NORMAL",
        ),
    )
    op.create_check_constraint(
        "ck_run_task_budget_liveness_credit",
        "run_task_token_budgets",
        "last_liveness_credit IN ('NORMAL', 'INITIAL_STARTUP', 'TOOL_RECOVERY', "
        "'VERIFIED_PROGRESS', 'CHECKPOINT_RESUME')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_run_task_budget_liveness_credit",
        "run_task_token_budgets",
        type_="check",
    )
    op.drop_column("run_task_token_budgets", "last_liveness_credit")
    op.drop_column("run_task_token_budgets", "tool_recovery_credit_used")
