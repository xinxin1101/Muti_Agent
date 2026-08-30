"""add encrypted project-scoped publication credentials

Revision ID: 0009_project_credentials
Revises: 0008_project_lifecycle
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_project_credentials"
down_revision: str | None = "0008_project_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgcrypto is bundled with the official PostgreSQL image and keeps plaintext out of the
    # durable table. The encryption key remains in the local ignored .env file, not PostgreSQL.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "project_credentials",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("github_publication_token_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
    )


def downgrade() -> None:
    op.drop_table("project_credentials")
