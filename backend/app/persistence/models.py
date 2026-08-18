from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class PersistenceBase(DeclarativeBase):
    pass


class ProjectRow(PersistenceBase):
    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    repository_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    runs: Mapped[list[RunRow]] = relationship(back_populates="project", cascade="all, delete-orphan")


class RunRow(PersistenceBase):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RUNNING")
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


class TaskRow(PersistenceBase):
    __tablename__ = "tasks"

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
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
