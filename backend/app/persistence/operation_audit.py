from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.operation_audit import (
    OperationAuditAction,
    OperationAuditOutcome,
    OperationAuditRecord,
)
from app.persistence.database import create_postgres_engine, create_session_factory


class PostgresOperationAuditStore:
    """Append-only audit log for confirmed management and recovery operations."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        owns_engine: bool = False,
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory or create_session_factory(engine)
        self._owns_engine = owns_engine

    @classmethod
    def from_url(cls, database_url: SecretStr | str, *, echo: bool = False):
        return cls(engine=create_postgres_engine(database_url, echo=echo), owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def record(
        self,
        *,
        operation_key: str,
        actor: str,
        action: OperationAuditAction,
        outcome: OperationAuditOutcome,
        project_id: UUID | None = None,
        run_id: UUID | None = None,
        development_session_id: UUID | None = None,
        impact_summary: dict | None = None,
        result_summary: str = "",
    ) -> OperationAuditRecord:
        audit_id = uuid4()
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO operation_audits "
                    "(id, operation_key, actor, action, outcome, project_id, run_id, "
                    "development_session_id, impact_summary, result_summary) "
                    "VALUES (:id, :operation_key, :actor, :action, :outcome, :project_id, "
                    ":run_id, :development_session_id, :impact_summary, :result_summary) "
                    "ON CONFLICT (operation_key) DO NOTHING"
                ),
                {
                    "id": audit_id,
                    "operation_key": operation_key,
                    "actor": actor,
                    "action": action.value,
                    "outcome": outcome.value,
                    "project_id": project_id,
                    "run_id": run_id,
                    "development_session_id": development_session_id,
                    "impact_summary": impact_summary or {},
                    "result_summary": result_summary[:512],
                },
            )
            row = (
                await session.execute(
                    text(
                        "SELECT id, operation_key, actor, action, outcome, project_id, run_id, "
                        "development_session_id, impact_summary, result_summary, created_at "
                        "FROM operation_audits WHERE operation_key = :operation_key"
                    ),
                    {"operation_key": operation_key},
                )
            ).mappings().one()
        return OperationAuditRecord(
            audit_id=row["id"],
            operation_key=row["operation_key"],
            actor=row["actor"],
            action=OperationAuditAction(row["action"]),
            outcome=OperationAuditOutcome(row["outcome"]),
            project_id=row["project_id"],
            run_id=row["run_id"],
            development_session_id=row["development_session_id"],
            impact_summary=row["impact_summary"],
            result_summary=row["result_summary"],
            created_at=row["created_at"],
        )
