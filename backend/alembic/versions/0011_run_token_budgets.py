"""add durable run token budgets and reservations

Revision ID: 0011_run_token_budgets
Revises: 0010_failure_explanations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_run_token_budgets"
down_revision: str | None = "0010_failure_explanations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_token_budgets",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("used_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="NORMAL"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        sa.CheckConstraint("total_budget_tokens > 0", name="ck_run_token_budgets_total_positive"),
        sa.CheckConstraint("used_prompt_tokens >= 0 AND used_completion_tokens >= 0 AND used_total_tokens >= 0 AND reserved_tokens >= 0", name="ck_run_token_budgets_nonnegative"),
        sa.CheckConstraint("used_total_tokens = used_prompt_tokens + used_completion_tokens", name="ck_run_token_budgets_total_shape"),
        sa.CheckConstraint("status IN ('NORMAL', 'WARNING', 'CRITICAL', 'EXHAUSTED')", name="ck_run_token_budgets_status"),
    )
    op.create_table(
        "run_token_reservations",
        sa.Column("reservation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.CheckConstraint("reserved_input_tokens >= 0 AND reserved_output_tokens > 0", name="ck_run_token_reservations_values"),
        sa.CheckConstraint("role IN ('planner', 'developer', 'reviewer', 'repair')", name="ck_run_token_reservations_role"),
    )
    op.create_index("ix_run_token_reservations_run", "run_token_reservations", ["run_id"])
    op.create_table(
        "run_token_role_usage",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "role"),
        sa.CheckConstraint("prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0 AND call_count >= 0", name="ck_run_token_role_usage_nonnegative"),
        sa.CheckConstraint("total_tokens = prompt_tokens + completion_tokens", name="ck_run_token_role_usage_total_shape"),
        sa.CheckConstraint("role IN ('planner', 'developer', 'reviewer', 'repair')", name="ck_run_token_role_usage_role"),
    )


def downgrade() -> None:
    op.drop_table("run_token_role_usage")
    op.drop_index("ix_run_token_reservations_run", table_name="run_token_reservations")
    op.drop_table("run_token_reservations")
    op.drop_table("run_token_budgets")
