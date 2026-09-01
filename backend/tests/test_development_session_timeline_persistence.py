from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.models.dag import TaskDAG, TaskNode
from app.models.development_session import DevelopmentSessionTimelineKind
from app.models.task import TaskContract
from app.persistence.development_session import PostgresDevelopmentSessionStore


class _RecordingSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters):  # type: ignore[no-untyped-def]
        self.calls.append((str(statement), dict(parameters)))
        return type("Result", (), {"rowcount": 1})()


class _RecordingSessionFactory:
    def __init__(self, session: _RecordingSession) -> None:
        self._session = session

    def begin(self) -> _RecordingSessionFactory:
        return self

    async def __aenter__(self) -> _RecordingSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(
                task=TaskContract(
                    task_id="core",
                    objective="实现核心模块。",
                    readable_files=(),
                    writable_files=("src/core.py",),
                    readonly_files=("tests/**",),
                    acceptance_criteria=("核心模块可验证。",),
                    verification_commands=("pytest tests/test_core.py -q",),
                )
            ),
        )
    )


def test_timeline_metadata_is_serialized_for_textual_jsonb_insert() -> None:
    session = _RecordingSession()
    store = object.__new__(PostgresDevelopmentSessionStore)

    asyncio.run(
        store._append_timeline_in_transaction(  # type: ignore[arg-type]
            session,
            session_id=uuid4(),
            event_key="user-requirement",
            kind=DevelopmentSessionTimelineKind.USER_REQUIREMENT,
            title="用户提出开发需求",
            metadata={"length": 10, "requirement_sha256": "a" * 64},
        )
    )

    statement, parameters = session.calls[0]
    assert "CAST(:metadata AS jsonb)" in statement
    assert json.loads(str(parameters["metadata"])) == {
        "length": 10,
        "requirement_sha256": "a" * 64,
    }


def test_planning_dag_payload_is_serialized_before_jsonb_update() -> None:
    session = _RecordingSession()
    store = object.__new__(PostgresDevelopmentSessionStore)
    store._session_factory = _RecordingSessionFactory(session)  # type: ignore[assignment]

    asyncio.run(store.record_plan(session_id=uuid4(), dag=_dag()))

    statement, parameters = session.calls[0]
    assert "dag_payload = CAST(:payload AS jsonb)" in statement
    assert json.loads(str(parameters["payload"]))["tasks"][0]["task"]["task_id"] == "core"
