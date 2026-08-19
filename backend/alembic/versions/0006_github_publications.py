"""add non-authoritative GitHub publication audit projection

Revision ID: 0006_github_publications
Revises: 0005_task_dag_dependencies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_github_publications"
down_revision: str | None = "0005_task_dag_dependencies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "github_publications",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intent", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("intent_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="READY"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pull_request_number", sa.Integer(), nullable=True),
        sa.Column("pull_request_url", sa.Text(), nullable=True),
        sa.Column("pull_request_state", sa.String(length=16), nullable=True),
        sa.Column("pull_request_draft", sa.Boolean(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=512), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_github_publications_attempt_count",
        ),
        sa.CheckConstraint(
            "state IN ('READY', 'FAILED', 'PUBLISHED')",
            name="ck_github_publications_state",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name="fk_github_publications_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_github_publications"),
    )


def downgrade() -> None:
    op.drop_table("github_publications")
