from __future__ import annotations

# ruff: noqa: E501
import json
from hashlib import sha256
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.dag import TaskDAG
from app.models.interface_contract import InterfaceContractGate, InterfaceContractState
from app.persistence.database import create_postgres_engine, create_session_factory


class PostgresInterfaceContractRegistry:
    """Persist and gate declared producer/consumer interfaces for one Run.

    DevFlow cannot safely infer arbitrary language symbols after the fact.  A contract is
    therefore satisfied only when its producer has accepted successful worker evidence; the
    producer's declared verification commands and accepted commit are kept with that fact.
    """

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
    ) -> PostgresInterfaceContractRegistry:
        return cls(engine=create_postgres_engine(database_url, echo=echo), owns_engine=True)

    async def dispose(self) -> None:
        if self._owns_engine:
            await self._engine.dispose()

    async def declare_for_dag(self, *, run_id: UUID, dag: TaskDAG) -> None:
        producers = {interface: node for node in dag.tasks for interface in node.produces}
        async with self._session_factory.begin() as session:
            for interface, node in producers.items():
                consumers = tuple(
                    consumer.task.task_id
                    for consumer in dag.tasks
                    if interface in consumer.consumes
                )
                if not consumers:
                    continue
                version = sha256(
                    (
                        interface
                        + "\n"
                        + node.task.task_id
                        + "\n"
                        + "\n".join(node.task.verification_commands)
                    ).encode()
                ).hexdigest()
                await session.execute(
                    text(
                        "INSERT INTO run_interface_contracts "
                        "(run_id, interface_id, producer_task_id, consumer_task_ids, "
                        "verification_commands, version_sha256) "
                        "VALUES (:run_id, :interface, :producer, CAST(:consumers AS jsonb), "
                        "CAST(:commands AS jsonb), :version) "
                        "ON CONFLICT (run_id, interface_id) DO NOTHING"
                    ),
                    {
                        "run_id": run_id,
                        "interface": interface,
                        "producer": node.task.task_id,
                        "consumers": json.dumps(consumers),
                        "commands": json.dumps(tuple(node.task.verification_commands)),
                        "version": version,
                    },
                )

    async def gate_for_task(self, *, run_id: UUID, task_id: str) -> InterfaceContractGate:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT interface_id, producer_task_id, state FROM run_interface_contracts "
                        "WHERE run_id = :run_id AND consumer_task_ids ? :task_id ORDER BY interface_id"
                    ),
                    {"run_id": run_id, "task_id": task_id},
                )
            ).all()
        missing = tuple(
            str(row.interface_id)
            for row in rows
            if row.state != InterfaceContractState.SATISFIED.value
        )
        producers = tuple(
            sorted(
                {
                    str(row.producer_task_id)
                    for row in rows
                    if row.state != InterfaceContractState.SATISFIED.value
                }
            )
        )
        if not missing:
            return InterfaceContractGate(task_id=task_id, allowed=True)
        return InterfaceContractGate(
            task_id=task_id,
            allowed=False,
            missing_interfaces=missing,
            producer_tasks=producers,
            reason="接口契约未满足：上游任务尚未验证并导出所需接口。",
        )

    async def mark_producer_satisfied(
        self, *, run_id: UUID, task_id: str, commit_sha: str | None
    ) -> None:
        if commit_sha is None:
            return
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE run_interface_contracts SET state = 'SATISFIED', commit_sha = :commit, "
                    "satisfied_at = now() WHERE run_id = :run_id AND producer_task_id = :task_id "
                    "AND state = 'DECLARED'"
                ),
                {"run_id": run_id, "task_id": task_id, "commit": commit_sha},
            )

    async def mark_producer_unmet(self, *, run_id: UUID, task_id: str) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "UPDATE run_interface_contracts SET state = 'UNMET' "
                    "WHERE run_id = :run_id AND producer_task_id = :task_id AND state = 'DECLARED'"
                ),
                {"run_id": run_id, "task_id": task_id},
            )
