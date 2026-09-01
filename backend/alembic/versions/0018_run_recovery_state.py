"""persist user-facing run liveness and recovery linkage"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_run_recovery_state"
down_revision: str | None = "0017_project_run_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("display_status", sa.String(length=32), nullable=False, server_default="RUNNING"),
    )
    op.create_check_constraint(
        "ck_runs_display_status",
        "runs",
        "display_status IN ('RUNNING','WAITING_EXTERNAL','RECOVERY_REQUIRED','FAILED','SUCCEEDED')",
    )
    op.add_column("runs", sa.Column("recovery_reason", sa.Text(), nullable=True))
    op.add_column(
        "runs",
        sa.Column("recovery_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runs",
        sa.Column("recovery_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_runs_recovery_run",
        "runs",
        "runs",
        ["recovery_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_runs_recovery_run_id",
        "runs",
        ["recovery_run_id"],
        unique=True,
        postgresql_where=sa.text("recovery_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_runs_recovery_run_id", table_name="runs")
    op.drop_constraint("fk_runs_recovery_run", "runs", type_="foreignkey")
    op.drop_column("runs", "recovery_run_id")
    op.drop_column("runs", "recovery_checked_at")
    op.drop_column("runs", "recovery_reason")
    op.drop_constraint("ck_runs_display_status", "runs", type_="check")
    op.drop_column("runs", "display_status")
