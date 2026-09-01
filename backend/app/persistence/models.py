from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class PersistenceBase(DeclarativeBase):
    pass


class ProjectRow(PersistenceBase):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint(
            "canonical_repository_url",
            "default_branch",
            name="uq_projects_repository_branch",
        ),
        CheckConstraint(
            "provision_status IN ('PROVISIONING', 'READY', 'FAILED', 'ARCHIVED')",
            name="ck_projects_provision_status",
        ),
        CheckConstraint(
            "(provision_status = 'FAILED' AND provision_error_code IS NOT NULL "
            "AND provision_error_message IS NOT NULL) OR "
            "(provision_status <> 'FAILED' AND provision_error_code IS NULL "
            "AND provision_error_message IS NULL)",
            name="ck_projects_provision_error_shape",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    repository_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_repository_url: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    provision_status: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    provision_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provision_error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_provisioned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    runs: Mapped[list[RunRow]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class ProjectCredentialRow(PersistenceBase):
    """Encrypted project-scoped credentials; plaintext never enters this table."""

    __tablename__ = "project_credentials"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    github_publication_token_ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class RunRow(PersistenceBase):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "event_sequence >= 0",
            name="ck_runs_event_sequence_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    display_status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
    recovery_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    visibility_status: Mapped[str] = mapped_column(String(16), nullable=False, default="VISIBLE")
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dag_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    terminal_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    terminal_result_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[ProjectRow] = relationship(back_populates="runs")
    tasks: Mapped[list[TaskRow]] = relationship(back_populates="run", cascade="all, delete-orphan")
    evidence: Mapped[list[EvidenceRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvidenceRow.id",
    )
    events: Mapped[list[RuntimeEventRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RuntimeEventRow.sequence",
    )


class TaskRow(PersistenceBase):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "("
            "lease_owner IS NULL AND lease_dispatch_id IS NULL "
            "AND lease_acquired_at IS NULL AND heartbeat_at IS NULL "
            "AND lease_until IS NULL AND lease_released_at IS NULL "
            "AND run_token IS NULL AND lease_generation = 0"
            ") OR ("
            "lease_owner IS NOT NULL AND lease_dispatch_id IS NOT NULL "
            "AND lease_acquired_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND lease_until IS NOT NULL AND run_token IS NOT NULL "
            "AND lease_generation >= 1"
            ")",
            name="ck_tasks_lease_shape",
        ),
        UniqueConstraint("run_token", name="uq_tasks_run_token"),
        Index("ix_tasks_lease_until", "lease_until"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    depends_on: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    node_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_dispatch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    lease_acquired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[RunRow] = relationship(back_populates="tasks")


class EvidenceRow(PersistenceBase):
    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("run_id", "evidence_key", name="uq_evidence_run_key"),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_evidence_task",
            ondelete="CASCADE",
        ),
        Index("ix_evidence_run_task_kind", "run_id", "task_id", "kind", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[RunRow] = relationship(back_populates="evidence")


class RuntimeEventRow(PersistenceBase):
    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_runtime_events_event_id"),
        UniqueConstraint("run_id", "event_key", name="uq_runtime_events_run_key"),
        UniqueConstraint("run_id", "sequence", name="uq_runtime_events_run_sequence"),
        ForeignKeyConstraint(
            ["run_id", "task_id"],
            ["tasks.run_id", "tasks.task_id"],
            name="fk_runtime_events_task",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "generation IS NULL OR generation >= 1",
            name="ck_runtime_events_generation",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="ck_runtime_events_schema_version",
        ),
        CheckConstraint(
            "task_id IS NOT NULL OR (dispatch_id IS NULL AND generation IS NULL)",
            name="ck_runtime_events_task_correlation",
        ),
        Index("ix_runtime_events_run_sequence", "run_id", "sequence"),
        Index("ix_runtime_events_run_task_sequence", "run_id", "task_id", "sequence"),
        Index("ix_runtime_events_dispatch", "dispatch_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dispatch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attributes_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[RunRow] = relationship(back_populates="events")


class GitHubPublicationRow(PersistenceBase):
    """External publication audit projection; never a Run-success authority."""

    __tablename__ = "github_publications"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_github_publications_attempt_count"),
        CheckConstraint(
            "state IN ('READY', 'PUBLISHING', 'FAILED', 'PUBLISHED')",
            name="ck_github_publications_state",
        ),
        CheckConstraint(
            "(state = 'PUBLISHING' AND attempt_token IS NOT NULL "
            "AND attempt_expires_at IS NOT NULL) OR "
            "(state <> 'PUBLISHING' AND attempt_token IS NULL "
            "AND attempt_expires_at IS NULL)",
            name="ck_github_publications_attempt_claim",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    intent: Mapped[dict] = mapped_column(JSONB, nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="READY")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pull_request_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pull_request_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pull_request_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pull_request_draft: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
