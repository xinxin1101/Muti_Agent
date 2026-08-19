from __future__ import annotations

from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.models import ProductProject, ProductRun
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.models import ProjectRow, RunRow, TaskRow


class PostgresProductCatalog:
    """Read-only product query boundary over accepted persistence rows."""

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
    def from_url(
        cls,
        database_url: SecretStr | str,
        *,
        echo: bool = False,
    ) -> PostgresProductCatalog:
        engine = create_postgres_engine(database_url, echo=echo)
        return cls(engine=engine, owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def list_projects(self, *, limit: int = 100) -> tuple[ProductProject, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("project query limit must be between 1 and 500")
        run_count = (
            select(func.count(RunRow.id))
            .where(RunRow.project_id == ProjectRow.id)
            .correlate(ProjectRow)
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ProjectRow, run_count.label("run_count"))
                    .order_by(ProjectRow.created_at.desc(), ProjectRow.id)
                    .limit(limit)
                )
            ).all()
        return tuple(
            ProductProject(
                project_id=project.id,
                repository_url=project.repository_url,
                default_branch=project.default_branch,
                created_at=project.created_at,
                run_count=count,
                workspace_ready=False,
            )
            for project, count in rows
        )

    async def get_project(self, project_id: UUID) -> ProductProject:
        run_count = (
            select(func.count(RunRow.id))
            .where(RunRow.project_id == ProjectRow.id)
            .correlate(ProjectRow)
            .scalar_subquery()
        )
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ProjectRow, run_count.label("run_count")).where(
                        ProjectRow.id == project_id
                    )
                )
            ).one_or_none()
        if row is None:
            raise ValueError(f"unknown persistence project: {project_id}")
        project, count = row
        return ProductProject(
            project_id=project.id,
            repository_url=project.repository_url,
            default_branch=project.default_branch,
            created_at=project.created_at,
            run_count=count,
            workspace_ready=False,
        )

    async def list_runs(
        self,
        *,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> tuple[ProductRun, ...]:
        if limit < 1 or limit > 500:
            raise ValueError("run query limit must be between 1 and 500")
        task_count = (
            select(func.count(TaskRow.task_id))
            .where(TaskRow.run_id == RunRow.id)
            .correlate(RunRow)
            .scalar_subquery()
        )
        statement = select(RunRow, task_count.label("task_count"))
        if project_id is not None:
            statement = statement.where(RunRow.project_id == project_id)
        statement = statement.order_by(RunRow.started_at.desc(), RunRow.id).limit(limit)
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(
            ProductRun(
                run_id=run.id,
                project_id=run.project_id,
                status=run.status,
                base_commit=run.base_commit,
                task_count=count,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
            for run, count in rows
        )
