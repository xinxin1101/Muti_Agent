"""add durable project lifecycle and branch-scoped identity

Revision ID: 0008_project_lifecycle
Revises: 0007_dispatch_attempts
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_project_lifecycle"
down_revision: str | None = "0007_dispatch_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("canonical_repository_url", sa.Text(), nullable=True))
    op.add_column(
        "projects",
        sa.Column(
            "provision_status",
            sa.String(length=32),
            server_default="READY",
            nullable=False,
        ),
    )
    op.add_column("projects", sa.Column("provision_error_code", sa.String(64), nullable=True))
    op.add_column("projects", sa.Column("provision_error_message", sa.String(512), nullable=True))
    op.add_column(
        "projects",
        sa.Column("last_provisioned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("projects", sa.Column("last_synced_commit", sa.String(64), nullable=True))

    op.execute(
        sa.text(
            "UPDATE projects SET canonical_repository_url = "
            "regexp_replace(regexp_replace(trim(repository_url), '/+$', ''), '\\.git$', '', 'i')"
        )
    )
    op.alter_column("projects", "canonical_repository_url", nullable=False)

    op.drop_constraint("projects_repository_url_key", "projects", type_="unique")
    op.create_unique_constraint(
        "uq_projects_repository_branch",
        "projects",
        ["canonical_repository_url", "default_branch"],
    )
    op.create_check_constraint(
        "ck_projects_provision_status",
        "projects",
        "provision_status IN ('PROVISIONING', 'READY', 'FAILED', 'ARCHIVED')",
    )
    op.create_check_constraint(
        "ck_projects_provision_error_shape",
        "projects",
        "(provision_status = 'FAILED' AND provision_error_code IS NOT NULL "
        "AND provision_error_message IS NOT NULL) OR "
        "(provision_status <> 'FAILED' AND provision_error_code IS NULL "
        "AND provision_error_message IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_projects_provision_error_shape", "projects", type_="check")
    op.drop_constraint("ck_projects_provision_status", "projects", type_="check")
    op.drop_constraint("uq_projects_repository_branch", "projects", type_="unique")
    op.create_unique_constraint("projects_repository_url_key", "projects", ["repository_url"])
    op.drop_column("projects", "last_synced_commit")
    op.drop_column("projects", "last_synced_at")
    op.drop_column("projects", "last_provisioned_at")
    op.drop_column("projects", "provision_error_message")
    op.drop_column("projects", "provision_error_code")
    op.drop_column("projects", "provision_status")
    op.drop_column("projects", "canonical_repository_url")
