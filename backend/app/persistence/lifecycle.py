from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.lifecycle import ProjectLifecycleState, RunVisibilityState
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.errors import PersistenceConflictError
from app.persistence.types import PersistedRunStatus


@dataclass(frozen=True)
class ProjectDeletionFacts:
    project_id: UUID
    required_confirmation_name: str
    run_count: int
    development_session_count: int
    local_credential_count: int


class PostgresLifecycleStore:
    """Persistence operations that never reinterpret immutable Run execution facts."""

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
        cls, database_url: SecretStr | str, *, echo: bool = False
    ) -> PostgresLifecycleStore:
        return cls(engine=create_postgres_engine(database_url, echo=echo), owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def archive_project(self, project_id: UUID) -> None:
        await self._set_project_lifecycle(
            project_id,
            expected=ProjectLifecycleState.ACTIVE,
            target=ProjectLifecycleState.ARCHIVED,
        )

    async def restore_project(self, project_id: UUID) -> None:
        await self._set_project_lifecycle(
            project_id,
            expected=ProjectLifecycleState.ARCHIVED,
            target=ProjectLifecycleState.ACTIVE,
        )

    async def ensure_project_active(self, project_id: UUID) -> None:
        async with self._session_factory() as session:
            lifecycle = (
                await session.execute(
                    text("SELECT lifecycle_state FROM projects WHERE id = :project_id"),
                    {"project_id": project_id},
                )
            ).scalar_one_or_none()
        if lifecycle is None:
            raise ValueError(f"unknown persistence project: {project_id}")
        if lifecycle != ProjectLifecycleState.ACTIVE.value:
            raise PersistenceConflictError("已归档或正在删除的项目不能创建新的运行")

    async def deletion_facts(self, project_id: UUID) -> ProjectDeletionFacts:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT p.repository_url, p.lifecycle_state, "
                            "(SELECT count(*) FROM runs r WHERE r.project_id = p.id) AS run_count, "
                            "(SELECT count(*) FROM development_sessions s "
                            " WHERE s.project_id = p.id) AS session_count, "
                            "(SELECT count(*) FROM project_credentials c "
                            " WHERE c.project_id = p.id) AS credential_count "
                            "FROM projects p WHERE p.id = :project_id"
                        ),
                        {"project_id": project_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"unknown persistence project: {project_id}")
        if row["lifecycle_state"] not in {
            ProjectLifecycleState.ACTIVE.value,
            ProjectLifecycleState.ARCHIVED.value,
        }:
            raise PersistenceConflictError("项目当前不允许删除")
        return ProjectDeletionFacts(
            project_id=project_id,
            required_confirmation_name=_project_display_name(row["repository_url"]),
            run_count=int(row["run_count"]),
            development_session_count=int(row["session_count"]),
            local_credential_count=int(row["credential_count"]),
        )

    async def ensure_deletion_allowed(self, project_id: UUID) -> ProjectDeletionFacts:
        facts = await self.deletion_facts(project_id)
        async with self._session_factory() as session:
            active = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM runs WHERE project_id = :project_id "
                        "AND status = :running"
                    ),
                    {"project_id": project_id, "running": PersistedRunStatus.RUNNING.value},
                )
            ).scalar_one()
            recovering = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM development_sessions WHERE project_id = :project_id "
                        "AND state IN ('PLANNING', 'READY_TO_RUN', 'RUNNING')"
                    ),
                    {"project_id": project_id},
                )
            ).scalar_one()
        if active:
            raise PersistenceConflictError("项目仍有运行中的 Run，不能永久删除")
        if recovering:
            raise PersistenceConflictError("项目正处于恢复或调度流程，不能永久删除")
        return facts

    async def delete_project(self, project_id: UUID) -> None:
        """Delete only local database facts. The caller owns filesystem cleanup and audit."""

        async with self._session_factory.begin() as session:
            result = await session.execute(
                text("DELETE FROM projects WHERE id = :project_id"), {"project_id": project_id}
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown persistence project: {project_id}")

    async def archive_run(self, run_id: UUID) -> bool:
        """Archive a terminal Run once; repeated archive requests are idempotent.

        ``False`` means an earlier request has already hidden the Run.  It is not a
        conflict: browser retries and rapid double-clicks must not turn a completed
        archive operation into an error.
        """
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE runs SET visibility_status = :archived WHERE id = :run_id "
                    "AND (status <> :running OR display_status = 'RECOVERY_REQUIRED') "
                    "AND visibility_status = :visible"
                ),
                {
                    "run_id": run_id,
                    "archived": RunVisibilityState.ARCHIVED.value,
                    "running": PersistedRunStatus.RUNNING.value,
                    "visible": RunVisibilityState.VISIBLE.value,
                },
            )
            if result.rowcount == 1:
                return True
            row = (
                await session.execute(
                    text(
                        "SELECT status, display_status, visibility_status FROM runs "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": run_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ValueError(f"unknown persistence run: {run_id}")
        if row["visibility_status"] == RunVisibilityState.ARCHIVED.value:
            return False
        raise PersistenceConflictError("只能归档已结束的 Run；请先停止或等待当前运行结束")

    async def _set_project_lifecycle(
        self,
        project_id: UUID,
        *,
        expected: ProjectLifecycleState,
        target: ProjectLifecycleState,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE projects SET lifecycle_state = :target WHERE id = :project_id "
                    "AND lifecycle_state = :expected"
                ),
                {"project_id": project_id, "expected": expected.value, "target": target.value},
            )
        if result.rowcount != 1:
            raise PersistenceConflictError("项目状态已变化，无法执行该操作")


class ProjectDeletionTokenSigner:
    """Stateless, short-lived confirmation tokens.

    Generating a deletion preview never writes to PostgreSQL.
    """

    def __init__(self, secret: SecretStr, *, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._secret = secret.get_secret_value().encode("utf-8")
        self._ttl = ttl

    def issue(
        self, facts: ProjectDeletionFacts, *, now: datetime | None = None
    ) -> tuple[str, datetime]:
        expires_at = (now or datetime.now(UTC)) + self._ttl
        payload = {
            "project_id": str(facts.project_id),
            "confirmation_name": facts.required_confirmation_name,
            "expires_at": int(expires_at.timestamp()),
        }
        encoded = _encode_payload(payload)
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}", expires_at

    def verify(
        self, token: str, facts: ProjectDeletionFacts, *, now: datetime | None = None
    ) -> None:
        try:
            encoded, provided_signature = token.rsplit(".", 1)
            expected_signature = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
            binascii.Error,
            TypeError,
            AttributeError,
        ) as exc:
            raise PersistenceConflictError("删除确认令牌无效") from exc
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise PersistenceConflictError("删除确认令牌无效")
        if payload.get("project_id") != str(facts.project_id):
            raise PersistenceConflictError("删除确认令牌不属于该项目")
        if payload.get("confirmation_name") != facts.required_confirmation_name:
            raise PersistenceConflictError("删除确认令牌与项目名称不匹配")
        if int(payload.get("expires_at", 0)) < int((now or datetime.now(UTC)).timestamp()):
            raise PersistenceConflictError("删除确认令牌已过期，请重新查看删除预览")


def _encode_payload(payload: dict[str, str | int]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _project_display_name(repository_url: str) -> str:
    path = urlparse(repository_url).path.strip("/")
    return path[:-4] if path.endswith(".git") else path
