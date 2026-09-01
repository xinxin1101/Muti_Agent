"""persist dependency-aware work-package lending decisions

Revision ID: 0021_budget_lending
Revises: 0020_prompt_calibration
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_budget_lending"
down_revision: str | None = "0020_prompt_calibration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "developer_startup_reserve_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    # Existing active Runs were created before startup reserves existed. Preserve one
    # normal turn for their downstream packages rather than treating their full pool
    # as loanable immediately after this migration.
    op.execute(
        "UPDATE run_task_token_budgets "
        "SET developer_startup_reserve_tokens = developer_budget_tokens / 2 "
        "WHERE developer_startup_reserve_tokens = 0 AND developer_budget_tokens > 0"
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("complexity_upgrade_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column(
            "last_downstream_available_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_budget_reason", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "run_task_token_budgets",
        sa.Column("last_recovery_action", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "run_token_budget_decisions",
        sa.Column("downstream_available_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "run_token_budget_decisions",
        sa.Column(
            "limit_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "run_token_budget_decisions",
        sa.Column("recovery_action", sa.String(length=64), nullable=True),
    )
    op.create_table(
        "run_task_budget_loans",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("borrower_task_id", sa.String(length=128), nullable=False),
        sa.Column("lender_task_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("amount_tokens", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id", "borrower_task_id"], ["tasks.run_id", "tasks.task_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "lender_task_id"], ["tasks.run_id", "tasks.task_id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("amount_tokens > 0", name="ck_run_task_budget_loan_positive"),
        sa.CheckConstraint("state IN ('ACTIVE', 'SETTLED')", name="ck_run_task_budget_loan_state"),
    )
    op.create_index(
        "ix_run_task_budget_loans_borrower",
        "run_task_budget_loans",
        ["run_id", "borrower_task_id", "state"],
    )
    op.create_index(
        "ix_run_task_budget_loans_lender",
        "run_task_budget_loans",
        ["run_id", "lender_task_id", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_task_budget_loans_lender", table_name="run_task_budget_loans")
    op.drop_index("ix_run_task_budget_loans_borrower", table_name="run_task_budget_loans")
    op.drop_table("run_task_budget_loans")
    for name in ("recovery_action", "limit_flags", "downstream_available_tokens"):
        op.drop_column("run_token_budget_decisions", name)
    for name in (
        "last_recovery_action",
        "last_budget_reason",
        "last_downstream_available_tokens",
        "complexity_upgrade_count",
        "developer_startup_reserve_tokens",
    ):
        op.drop_column("run_task_token_budgets", name)
