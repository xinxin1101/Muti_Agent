"""add interface contract gates and hierarchical token budgets

Revision ID: 0013_interface_contracts_and_hierarchical_budgets
Revises: 0012_planning_token_budgets
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_contract_budgets"
down_revision: str | None = "0012_planning_token_budgets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("node_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table(
        "run_interface_contracts",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interface_id", sa.String(length=256), nullable=False),
        sa.Column("producer_task_id", sa.String(length=128), nullable=False),
        sa.Column("consumer_task_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("verification_commands", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="DECLARED"),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("version_sha256", sa.String(length=64), nullable=True),
        sa.Column("satisfied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "interface_id"),
        sa.CheckConstraint("state IN ('DECLARED', 'SATISFIED', 'UNMET')", name="ck_run_interface_contract_state"),
    )
    op.create_index("ix_run_interface_contracts_consumer", "run_interface_contracts", ["run_id", "producer_task_id"])
    op.create_table(
        "run_stage_token_budgets",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("total_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("used_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "stage"),
        sa.CheckConstraint("total_budget_tokens >= 0 AND used_tokens >= 0 AND reserved_tokens >= 0", name="ck_run_stage_token_budget_nonnegative"),
    )
    op.create_table(
        "run_task_token_budgets",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("complexity", sa.String(length=16), nullable=False),
        sa.Column("total_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("developer_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("repair_budget_tokens", sa.Integer(), nullable=False),
        sa.Column("developer_used_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repair_used_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("developer_reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("repair_reserved_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.ForeignKeyConstraint(["run_id", "task_id"], ["tasks.run_id", "tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "task_id"),
        sa.CheckConstraint("complexity IN ('LOW', 'MEDIUM', 'HIGH')", name="ck_run_task_budget_complexity"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RECLAIMED')", name="ck_run_task_budget_status"),
        sa.CheckConstraint("total_budget_tokens >= 0 AND developer_budget_tokens >= 0 AND repair_budget_tokens >= 0", name="ck_run_task_budget_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("run_task_token_budgets")
    op.drop_table("run_stage_token_budgets")
    op.drop_index("ix_run_interface_contracts_consumer", table_name="run_interface_contracts")
    op.drop_table("run_interface_contracts")
    op.drop_column("tasks", "node_metadata")
