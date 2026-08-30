"""add flex borrowing facts to hierarchical token budgets

Revision ID: 0014_flex_work_package_budgets
Revises: 0013_contract_budgets
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0014_flex_work_package_budgets"
down_revision: str | None = "0013_contract_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_task_token_budgets",
        sa.Column("developer_borrowed_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("repair_borrowed_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("developer_reclaimed_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("repair_reclaimed_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("borrow_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_required_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_available_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_flex_available_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_borrowed_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_budget_decision", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "run_token_budget_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("required_tokens", sa.Integer(), nullable=False),
        sa.Column("package_available_tokens", sa.Integer(), nullable=False),
        sa.Column("flex_available_tokens", sa.Integer(), nullable=False),
        sa.Column("borrowed_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["tasks.run_id", "tasks.task_id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_run_token_budget_decisions_run_task",
        "run_token_budget_decisions",
        ["run_id", "task_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_token_budget_decisions_run_task", table_name="run_token_budget_decisions")
    op.drop_table("run_token_budget_decisions")
    for name in (
        "last_budget_decision",
        "last_borrowed_tokens",
        "last_flex_available_tokens",
        "last_available_tokens",
        "last_required_tokens",
        "borrow_count",
        "repair_reclaimed_tokens",
        "developer_reclaimed_tokens",
        "repair_borrowed_tokens",
        "developer_borrowed_tokens",
    ):
        op.drop_column("run_task_token_budgets", name)
