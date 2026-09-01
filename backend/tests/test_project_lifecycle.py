from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from app.persistence.errors import PersistenceConflictError
from app.persistence.lifecycle import (
    PostgresLifecycleStore,
    ProjectDeletionFacts,
    ProjectDeletionTokenSigner,
)
from app.workspace.lifecycle import LocalProjectArtifacts, LocalProjectCleanupError


def test_deletion_token_is_bound_to_project_name_and_expires() -> None:
    facts = ProjectDeletionFacts(
        project_id=uuid4(),
        required_confirmation_name="owner/repository",
        run_count=0,
        development_session_count=0,
        local_credential_count=1,
    )
    now = datetime(2026, 8, 31, tzinfo=UTC)
    signer = ProjectDeletionTokenSigner(SecretStr("local-test-secret"), ttl=timedelta(minutes=1))
    token, _ = signer.issue(facts, now=now)
    signer.verify(token, facts, now=now)
    with pytest.raises(PersistenceConflictError, match="过期"):
        signer.verify(token, facts, now=now + timedelta(minutes=2))


def test_local_artifacts_refuse_symlink_cleanup(tmp_path: Path) -> None:
    repositories = tmp_path / "repos"
    caches = tmp_path / "caches"
    repositories.mkdir()
    caches.mkdir()
    project_id = uuid4()
    target = repositories / str(project_id)
    target.mkdir()
    (target / "file.txt").write_text("safe", encoding="utf-8")
    artifacts = LocalProjectArtifacts(repositories, caches)
    assert artifacts.sizes(project_id)[0] == 4
    assert artifacts.remove(project_id) == (4, 0)
    assert not target.exists()

    link = repositories / str(uuid4())
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows test environment cannot create symlinks: {exc}")
    with pytest.raises(LocalProjectCleanupError, match="symbolic"):
        artifacts.remove(UUID(str(link.name)))


class _ArchiveResult:
    def __init__(self, *, rowcount: int = 0, row: dict[str, str] | None = None) -> None:
        self.rowcount = rowcount
        self._row = row

    def mappings(self) -> _ArchiveResult:
        return self

    def one_or_none(self) -> dict[str, str] | None:
        return self._row


class _ArchiveSession:
    def __init__(self, row: dict[str, str]) -> None:
        self._row = row
        self.calls = 0

    async def execute(self, _statement, _parameters):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return _ArchiveResult(rowcount=0)
        return _ArchiveResult(row=self._row)


class _ArchiveSessionFactory:
    def __init__(self, session: _ArchiveSession) -> None:
        self._session = session

    def begin(self) -> _ArchiveSessionFactory:
        return self

    async def __aenter__(self) -> _ArchiveSession:
        return self._session

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_archiving_an_already_archived_run_is_idempotent() -> None:
    store = object.__new__(PostgresLifecycleStore)
    store._session_factory = _ArchiveSessionFactory(  # type: ignore[assignment]
        _ArchiveSession(
            {
                "status": "FAILED",
                "display_status": "FAILED",
                "visibility_status": "ARCHIVED",
            }
        )
    )

    assert asyncio.run(store.archive_run(uuid4())) is False


def test_archiving_an_active_visible_run_remains_a_conflict() -> None:
    store = object.__new__(PostgresLifecycleStore)
    store._session_factory = _ArchiveSessionFactory(  # type: ignore[assignment]
        _ArchiveSession(
            {
                "status": "RUNNING",
                "display_status": "RUNNING",
                "visibility_status": "VISIBLE",
            }
        )
    )

    with pytest.raises(PersistenceConflictError, match="只能归档已结束"):
        asyncio.run(store.archive_run(uuid4()))
