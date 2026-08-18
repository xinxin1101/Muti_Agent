"""create PostgreSQL runtime evidence schema

Revision ID: 0001_postgresql_evidence
Revises:
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_postgresql_evidence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_url", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository_url"),
    )

    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("terminal_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("terminal_result_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_project_id", "runs", ["project_id"], unique=False)

    op.create_table(
        "tasks",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=False),
        sa.Column("contract", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contract_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "task_id"),
    )

    op.create_table(
        "evidence_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("evidence_key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_evidence_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "evidence_key", name="uq_evidence_run_key"),
    )
    op.create_index(
        "ix_evidence_run_task_kind",
        "evidence_records",
        ["run_id", "task_id", "kind", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_run_task_kind", table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_table("tasks")
    op.drop_index("ix_runs_project_id", table_name="runs")
    op.drop_table("runs")
    op.drop_table("projects")
