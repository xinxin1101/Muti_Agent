"""persist bounded per-turn work-package cost observations

Revision ID: 0022_cost_predictions
Revises: 0021_budget_lending
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_cost_predictions"
down_revision: str | None = "0021_budget_lending"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_task_cost_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("request_estimated_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("actual_completion_tokens", sa.Integer(), nullable=False),
        sa.Column("tool_argument_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "write_patch_argument_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("tool_result_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context_growth_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_real_progress", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"], ["tasks.run_id", "tasks.task_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "iteration >= 0 AND request_estimated_tokens >= 0 AND actual_prompt_tokens >= 0 "
            "AND actual_completion_tokens >= 0 AND tool_argument_tokens >= 0 "
            "AND write_patch_argument_tokens >= 0 "
            "AND tool_result_tokens >= 0 AND context_growth_tokens >= 0",
            name="ck_run_task_cost_observation_nonnegative",
        ),
    )
    op.create_index(
        "ix_run_task_cost_observations_lookup",
        "run_task_cost_observations",
        ["run_id", "task_id", "role", "id"],
    )
    for prefix in ("developer", "repair"):
        op.add_column(
            "run_task_token_budgets",
            sa.Column(
                f"{prefix}_predicted_next_input_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        op.add_column(
            "run_task_token_budgets",
            sa.Column(
                f"{prefix}_estimated_executable_turns",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_cost_prediction_reason", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_task_token_budgets", "last_cost_prediction_reason")
    for prefix in ("repair", "developer"):
        op.drop_column("run_task_token_budgets", f"{prefix}_estimated_executable_turns")
        op.drop_column("run_task_token_budgets", f"{prefix}_predicted_next_input_tokens")
    op.drop_index("ix_run_task_cost_observations_lookup", table_name="run_task_cost_observations")
    op.drop_table("run_task_cost_observations")
