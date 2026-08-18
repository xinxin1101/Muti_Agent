"""add structured runtime event timeline

Revision ID: 0004_structured_runtime_events
Revises: 0003_run_token_fencing
Create Date: 2026-08-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_structured_runtime_events"
down_revision = "0003_run_token_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "event_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_runs_event_sequence_nonnegative",
        "runs",
        "event_sequence >= 0",
    )

    op.create_table(
        "runtime_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("task_id", sa.String(length=128), nullable=True),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attributes_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "generation IS NULL OR generation >= 1",
            name="ck_runtime_events_generation",
        ),
        sa.CheckConstraint(
            "task_id IS NOT NULL OR (dispatch_id IS NULL AND generation IS NULL)",
            name="ck_runtime_events_task_correlation",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_runtime_events_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_runtime_events_task",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_runtime_events_event_id"),
        sa.UniqueConstraint("run_id", "event_key", name="uq_runtime_events_run_key"),
        sa.UniqueConstraint(
            "run_id",
            "sequence",
            name="uq_runtime_events_run_sequence",
        ),
    )
    op.create_index(
        "ix_runtime_events_run_sequence",
        "runtime_events",
        ["run_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_events_run_task_sequence",
        "runtime_events",
        ["run_id", "task_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_events_dispatch",
        "runtime_events",
        ["dispatch_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_events_dispatch", table_name="runtime_events")
    op.drop_index("ix_runtime_events_run_task_sequence", table_name="runtime_events")
    op.drop_index("ix_runtime_events_run_sequence", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_constraint("ck_runs_event_sequence_nonnegative", "runs", type_="check")
    op.drop_column("runs", "event_sequence")
