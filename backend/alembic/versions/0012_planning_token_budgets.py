"""add durable pre-run planning token budgets

Revision ID: 0012_planning_token_budgets
Revises: 0011_run_token_budgets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_planning_token_budgets"
down_revision: str | None = "0011_run_token_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_token_budgets",
        sa.Column("launch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("total_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("used_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("enable_thinking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="NORMAL"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("launch_id"),
        sa.UniqueConstraint("run_id", name="uq_planning_token_budgets_run"),
        sa.CheckConstraint(
            "total_budget_tokens > 0", name="ck_planning_token_budgets_total_positive"
        ),
        sa.CheckConstraint(
            "max_attempts > 0 AND attempt_count >= 0", name="ck_planning_token_budgets_attempts"
        ),
        sa.CheckConstraint(
            "used_prompt_tokens >= 0 AND used_completion_tokens >= 0 "
            "AND used_total_tokens >= 0 AND reserved_tokens >= 0",
            name="ck_planning_token_budgets_nonnegative",
        ),
        sa.CheckConstraint(
            "used_total_tokens = used_prompt_tokens + used_completion_tokens",
            name="ck_planning_token_budgets_total_shape",
        ),
        sa.CheckConstraint(
            "status IN ('NORMAL', 'WARNING', 'CRITICAL', 'EXHAUSTED')",
            name="ck_planning_token_budgets_status",
        ),
    )
    op.create_index("ix_planning_token_budgets_project", "planning_token_budgets", ["project_id"])
    op.create_table(
        "planning_token_reservations",
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("launch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["launch_id"], ["planning_token_budgets.launch_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.CheckConstraint(
            "reserved_input_tokens >= 0 AND reserved_output_tokens > 0",
            name="ck_planning_token_reservations_values",
        ),
    )


def downgrade() -> None:
    op.drop_table("planning_token_reservations")
    op.drop_index("ix_planning_token_budgets_project", table_name="planning_token_budgets")
    op.drop_table("planning_token_budgets")
