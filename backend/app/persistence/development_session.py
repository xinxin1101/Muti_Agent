from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.dag import TaskDAG
from app.models.development_session import (
    DevelopmentSession,
    DevelopmentSessionState,
    DevelopmentSessionTimelineEntry,
    DevelopmentSessionTimelineKind,
    DevelopmentWorkPackageProgress,
    DevelopmentWorkPackageState,
)
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.serialization import canonical_payload
from app.persistence.types import PersistedRunSnapshot, PersistenceEvidenceKind

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk|ghp|github_pat|bearer)[_-]?[a-z0-9][a-z0-9_.-]{7,}\b"
    r"|\b(?:api[_-]?key|token|authorization)\s*[=:]\s*\S+"
)


class PostgresDevelopmentSessionStore:
    """Durable planning/recovery facts that deliberately sit outside immutable Run evidence."""

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

    @asynccontextmanager
    async def continuation_lock(self, session_id: UUID):
        """Serialize recovery decisions without retaining any mutable Run state in the browser."""

        async with self._engine.connect() as connection, connection.begin():
            await connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:session_key))"),
                {"session_key": str(session_id)},
            )
            yield

    async def create(
        self,
        *,
        project_id: UUID,
        requirement: str,
        base_commit: str,
        repository_context_sha256: str,
        planning_launch_id: UUID | None,
    ) -> UUID:
        session_id = uuid4()
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "INSERT INTO development_sessions "
                    "(id, project_id, requirement, base_commit, repository_context_sha256, "
                    "state, planning_launch_id) VALUES "
                    "(:id, :project_id, :requirement, :base_commit, :context_sha, "
                    "'PLANNING', :launch_id)"
                ),
                {
                    "id": session_id,
                    "project_id": project_id,
                    "requirement": requirement,
                    "base_commit": base_commit,
                    "context_sha": repository_context_sha256,
                    "launch_id": planning_launch_id,
                },
            )
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key="user-requirement",
                kind=DevelopmentSessionTimelineKind.USER_REQUIREMENT,
                title="用户提出开发需求",
                detail="需求已保存；时间线不复制原始长文本或任何凭据。",
                metadata={
                    "requirement_sha256": hashlib.sha256(requirement.encode("utf-8")).hexdigest(),
                    "length": len(requirement),
                },
            )
        return session_id

    async def record_plan(self, *, session_id: UUID, dag: TaskDAG) -> None:
        payload, digest = canonical_payload(dag)
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE development_sessions SET dag_payload = CAST(:payload AS jsonb), "
                    "dag_sha256 = :digest, "
                    "state = 'READY_TO_RUN', planning_diagnostic = '', updated_at = now() "
                    "WHERE id = :session_id"
                ),
                {
                    "session_id": session_id,
                    "payload": self._jsonb_value(payload),
                    "digest": digest,
                },
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown development session: {session_id}")
            for node in dag.tasks:
                await session.execute(
                    text(
                        "INSERT INTO development_session_work_packages (session_id, task_id) "
                        "VALUES (:session_id, :task_id) "
                        "ON CONFLICT (session_id, task_id) DO NOTHING"
                    ),
                    {"session_id": session_id, "task_id": node.task.task_id},
                )
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key=f"plan:{digest}",
                kind=DevelopmentSessionTimelineKind.PLAN_DRAFT,
                title="规划草案已通过校验",
                detail=f"已生成 {len(dag.tasks)} 个可执行工作包。",
                metadata={
                    "dag_sha256": digest,
                    "task_ids": [node.task.task_id for node in dag.tasks],
                },
            )

    async def mark_planning_problem(
        self,
        *,
        session_id: UUID,
        diagnostic: str,
        reusable_plan: bool,
    ) -> None:
        state = (
            DevelopmentSessionState.PAUSED_PLANNING
            if reusable_plan
            else DevelopmentSessionState.PLANNING_FAILED
        )
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE development_sessions SET state = :state, "
                    "planning_diagnostic = :diagnostic, "
                    "updated_at = now() WHERE id = :session_id"
                ),
                {
                    "session_id": session_id,
                    "state": state.value,
                    "diagnostic": diagnostic[:1024],
                },
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown development session: {session_id}")
            diagnostic_hash = hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key=f"planning-problem:{state.value}:{diagnostic_hash}",
                kind=DevelopmentSessionTimelineKind.BUDGET_DIAGNOSTIC,
                title=("规划等待处理" if reusable_plan else "规划未能继续"),
                detail=self._safe_detail(diagnostic),
                metadata={"state": state.value, "reusable_plan": reusable_plan},
            )

    async def attach_run(
        self,
        *,
        session_id: UUID,
        run_id: UUID,
        resumed_from_run_id: UUID | None = None,
    ) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE development_sessions SET state = 'RUNNING', latest_run_id = :run_id, "
                    "resumed_from_run_id = COALESCE(:resumed_from, resumed_from_run_id), "
                    "updated_at = now() "
                    "WHERE id = :session_id"
                ),
                {"session_id": session_id, "run_id": run_id, "resumed_from": resumed_from_run_id},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown development session: {session_id}")
            await session.execute(
                text(
                    "UPDATE runs SET development_session_id = :session_id, "
                    "resumed_from_run_id = :resumed_from WHERE id = :run_id"
                ),
                {"session_id": session_id, "run_id": run_id, "resumed_from": resumed_from_run_id},
            )
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key=f"run-linked:{run_id}",
                kind=DevelopmentSessionTimelineKind.RUN_LINKED,
                title="已创建关联运行",
                detail=(
                    "已从旧运行恢复未完成工作包。"
                    if resumed_from_run_id
                    else "规划已进入执行队列。"
                ),
                run_id=run_id,
                metadata={
                    "resumed_from_run_id": (
                        None if resumed_from_run_id is None else str(resumed_from_run_id)
                    )
                },
            )

    async def snapshot(self, session_id: UUID) -> DevelopmentSession:
        async with self._session_factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT id, project_id, requirement, base_commit, "
                            "repository_context_sha256, state, dag_payload, dag_sha256, "
                            "planning_diagnostic, planning_launch_id, latest_run_id, "
                            "resumed_from_run_id, created_at, updated_at "
                            "FROM development_sessions WHERE id = :id"
                        ),
                        {"id": session_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise ValueError(f"unknown development session: {session_id}")
            packages = (
                (
                    await session.execute(
                        text(
                            "SELECT task_id, state, source_run_id, commit_sha, "
                            "completed_interfaces, verification_summary, failure_summary, "
                            "remaining_budget_tokens, context_state "
                            "FROM development_session_work_packages "
                            "WHERE session_id = :id ORDER BY task_id"
                        ),
                        {"id": session_id},
                    )
                )
                .mappings()
                .all()
            )
        return DevelopmentSession(
            session_id=row["id"],
            project_id=row["project_id"],
            requirement=row["requirement"],
            base_commit=row["base_commit"],
            repository_context_sha256=row["repository_context_sha256"],
            state=DevelopmentSessionState(row["state"]),
            dag=None if row["dag_payload"] is None else TaskDAG.model_validate(row["dag_payload"]),
            planning_diagnostic=row["planning_diagnostic"],
            planning_launch_id=row["planning_launch_id"],
            latest_run_id=row["latest_run_id"],
            resumed_from_run_id=row["resumed_from_run_id"],
            work_packages=tuple(
                DevelopmentWorkPackageProgress(
                    task_id=item["task_id"],
                    state=DevelopmentWorkPackageState(item["state"]),
                    source_run_id=item["source_run_id"],
                    commit_sha=item["commit_sha"],
                    completed_interfaces=tuple(item["completed_interfaces"] or []),
                    verification_summary=item["verification_summary"],
                    failure_summary=item["failure_summary"],
                    remaining_budget_tokens=item["remaining_budget_tokens"],
                    context_state=item["context_state"],
                )
                for item in packages
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_for_project(
        self, project_id: UUID, *, limit: int = 100
    ) -> tuple[DevelopmentSession, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("development session query limit must be between 1 and 200")
        async with self._session_factory() as session:
            session_ids = (
                await session.execute(
                    text(
                        "SELECT id FROM development_sessions WHERE project_id = :project_id "
                        "ORDER BY updated_at DESC, id DESC LIMIT :limit"
                    ),
                    {"project_id": project_id, "limit": limit},
                )
            ).scalars().all()
        # ``await`` inside a generator expression creates an async generator, which cannot be
        # consumed by ``tuple()``.  Resolve bounded rows before returning the immutable view.
        sessions: list[DevelopmentSession] = []
        for session_id in session_ids:
            sessions.append(await self.snapshot(session_id))
        return tuple(sessions)

    async def list_timeline(
        self, session_id: UUID, *, limit: int = 200
    ) -> tuple[DevelopmentSessionTimelineEntry, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("development session timeline limit must be between 1 and 500")
        async with self._session_factory() as session:
            exists = await session.scalar(
                text("SELECT 1 FROM development_sessions WHERE id = :session_id"),
                {"session_id": session_id},
            )
            if exists is None:
                raise ValueError(f"unknown development session: {session_id}")
            rows = (
                await session.execute(
                    text(
                        "SELECT id, session_id, kind, title, detail, run_id, task_id, metadata, "
                        "created_at FROM development_session_timeline_entries "
                        "WHERE session_id = :session_id ORDER BY created_at, id LIMIT :limit"
                    ),
                    {"session_id": session_id, "limit": limit},
                )
            ).mappings().all()
        return tuple(
            DevelopmentSessionTimelineEntry(
                entry_id=row["id"],
                session_id=row["session_id"],
                kind=DevelopmentSessionTimelineKind(row["kind"]),
                title=row["title"],
                detail=row["detail"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                metadata=row["metadata"] or {},
                created_at=row["created_at"],
            )
            for row in rows
        )

    async def append_timeline(
        self,
        *,
        session_id: UUID,
        event_key: str,
        kind: DevelopmentSessionTimelineKind,
        title: str,
        detail: str = "",
        run_id: UUID | None = None,
        task_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        async with self._session_factory.begin() as session:
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key=event_key,
                kind=kind,
                title=title,
                detail=detail,
                run_id=run_id,
                task_id=task_id,
                metadata=metadata,
            )

    async def find_session_id_by_run(self, run_id: UUID) -> UUID | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    text("SELECT development_session_id FROM runs WHERE id = :run_id"),
                    {"run_id": run_id},
                )
            ).scalar_one_or_none()

    async def capture_run_progress(
        self, *, session_id: UUID, snapshot: PersistedRunSnapshot
    ) -> None:
        """Project bounded accepted Worker evidence into resumable work-package state."""

        latest: dict[str, WorkerExecutionEvidence] = {}
        for evidence in snapshot.evidence:
            if (
                evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION
                or evidence.task_id is None
            ):
                continue
            payload = WorkerExecutionEvidence.model_validate(evidence.payload)
            latest[evidence.task_id] = payload
        async with self._session_factory.begin() as session:
            for task_id, execution in latest.items():
                checkpoint = execution.checkpoint
                if execution.status is WorkerExecutionStatus.SUCCEEDED:
                    state = DevelopmentWorkPackageState.SUCCEEDED
                    commit_sha = execution.commit_sha
                    completed_interfaces: tuple[str, ...] = ()
                    verification_summary = "验证通过。"
                    failure_summary = ""
                    remaining = None
                    context_state = None
                elif checkpoint is not None:
                    state = DevelopmentWorkPackageState.CHECKPOINTED
                    commit_sha = checkpoint.commit_sha
                    completed_interfaces = checkpoint.completed_interfaces
                    verification_summary = checkpoint.verification_summary
                    failure_summary = checkpoint.failure_summary
                    remaining = checkpoint.remaining_budget_tokens
                    context_state = (
                        None
                        if checkpoint.context_state is None
                        else checkpoint.context_state.model_dump(mode="json")
                    )
                else:
                    state = DevelopmentWorkPackageState.FAILED
                    commit_sha = None
                    completed_interfaces = ()
                    verification_summary = ""
                    failure_summary = "; ".join(item.message for item in execution.failures)[:512]
                    remaining = None
                    context_state = None
                await session.execute(
                    text(
                        "UPDATE development_session_work_packages SET state = :state, "
                        "source_run_id = :run_id, commit_sha = :commit_sha, "
                        "completed_interfaces = CAST(:interfaces AS jsonb), "
                        "verification_summary = :verification, "
                        "failure_summary = :failure, remaining_budget_tokens = :remaining, "
                        "context_state = CAST(:context_state AS jsonb), updated_at = now() "
                        "WHERE session_id = :session_id AND task_id = :task_id"
                    ),
                    {
                        "session_id": session_id,
                        "task_id": task_id,
                        "state": state.value,
                        "run_id": snapshot.run_id,
                        "commit_sha": commit_sha,
                        "interfaces": self._jsonb_value(list(completed_interfaces)),
                        "verification": verification_summary[:512],
                        "failure": failure_summary[:512],
                        "remaining": remaining,
                        "context_state": self._jsonb_value(context_state),
                    },
                )
                if state is DevelopmentWorkPackageState.SUCCEEDED:
                    kind = DevelopmentSessionTimelineKind.WORK_PACKAGE_SUCCEEDED
                    title = f"工作包 {task_id} 已完成"
                    detail = "验证已通过，可供下游工作包复用。"
                elif state is DevelopmentWorkPackageState.CHECKPOINTED:
                    kind = DevelopmentSessionTimelineKind.WORK_PACKAGE_CHECKPOINTED
                    title = f"工作包 {task_id} 已保存检查点"
                    detail = self._safe_detail(
                        verification_summary or "时间切片结束，等待后续续接。"
                    )
                else:
                    kind = DevelopmentSessionTimelineKind.WORK_PACKAGE_FAILED
                    title = f"工作包 {task_id} 未完成"
                    detail = self._safe_detail(failure_summary or "未保存可恢复检查点。")
                await self._append_timeline_in_transaction(
                    session,
                    session_id=session_id,
                    event_key=f"work-package:{snapshot.run_id}:{task_id}:{state.value}",
                    kind=kind,
                    title=title,
                    detail=detail,
                    run_id=snapshot.run_id,
                    task_id=task_id,
                    metadata={"state": state.value, "commit_sha": commit_sha},
                )

    async def mark_completed(self, session_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                text(
                    "UPDATE development_sessions SET state = 'COMPLETED', updated_at = now() "
                    "WHERE id = :session_id"
                ),
                {"session_id": session_id},
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown development session: {session_id}")
            await self._append_timeline_in_transaction(
                session,
                session_id=session_id,
                event_key="session-completed",
                kind=DevelopmentSessionTimelineKind.USER_ACTION,
                title="开发会话已完成",
                detail="所有工作包均已有成功验证证据。",
            )

    async def _append_timeline_in_transaction(
        self,
        session: AsyncSession,
        *,
        session_id: UUID,
        event_key: str,
        kind: DevelopmentSessionTimelineKind,
        title: str,
        detail: str = "",
        run_id: UUID | None = None,
        task_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO development_session_timeline_entries "
                "(session_id, event_key, kind, title, detail, run_id, task_id, metadata) "
                "VALUES (:session_id, :event_key, :kind, :title, :detail, :run_id, :task_id, "
                "CAST(:metadata AS jsonb)) ON CONFLICT (session_id, event_key) DO NOTHING"
            ),
            {
                "session_id": session_id,
                "event_key": event_key[:255],
                "kind": kind.value,
                "title": title[:160],
                "detail": self._safe_detail(detail),
                "run_id": run_id,
                "task_id": task_id,
                # ``text()`` has no JSONB bind type, so psycopg cannot adapt a Python dict
                # here. Serialize the bounded observability payload explicitly and let
                # PostgreSQL validate/cast it as JSONB.
                "metadata": self._jsonb_value(metadata or {}),
            },
        )

    @staticmethod
    def _jsonb_value(value: object | None) -> str | None:
        """Adapt bounded JSON-compatible values for textual PostgreSQL statements."""

        if value is None:
            return None
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _safe_detail(value: str) -> str:
        return _SECRET_VALUE_RE.sub("[已隐藏凭据]", value)[:512]
