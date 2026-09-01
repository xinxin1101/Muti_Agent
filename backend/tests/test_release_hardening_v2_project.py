import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.repository_context import RepositoryPlanningContextBuilder
from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models.dispatch import TaskDispatchEnvelope
from app.models.project import canonical_repository_url
from app.persistence.project import ProjectAwarePostgresEvidenceStore
from app.workers.project_identity import ProjectIdentityValidatingQueuedTaskWorker
from app.workspace import LocalGitWorkspace, ManagedProjectProvisioner


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def test_canonical_repository_url_is_branch_identity_input() -> None:
    assert (
        canonical_repository_url("HTTPS://GitHub.com/Acme/Demo.git/")
        == "https://github.com/Acme/Demo"
    )


def test_planning_context_uses_frozen_metadata_without_source_blobs() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.email", "devflow@example.invalid")
        _git(root, "config", "user.name", "DevFlow Test")
        (root / "README.md").write_text("auth behavior: OLD\n", encoding="utf-8")
        old_commit = _commit(root, "old")
        (root / "README.md").write_text("auth behavior: NEW\n", encoding="utf-8")
        _commit(root, "new")

        context = RepositoryPlanningContextBuilder().build(
            LocalGitWorkspace(root),
            base_commit=old_commit,
            requirement="update auth behavior",
            repository_url="https://github.com/acme/demo",
            default_branch="main",
        )

        assert "README.md" in context
        assert "auth behavior: OLD" not in context
        assert "auth behavior: NEW" not in context
        assert f"base_commit={old_commit}" in context
        assert "context_kind=metadata_only" in context
        assert "source_files_included=false" in context


def test_planning_context_does_not_read_non_utf8_source_blobs() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        _git(root, "init", "-b", "main")
        _git(root, "config", "user.email", "devflow@example.invalid")
        _git(root, "config", "user.name", "DevFlow Test")
        # 0x94 is invalid in UTF-8 and previously crashed `subprocess.run(text=True)` on
        # Windows installations whose default process encoding is GBK.
        (root / "README.md").write_bytes(b"repository note: \x94\n")
        commit = _commit(root, "non-utf8 readme")

        context = RepositoryPlanningContextBuilder().build(
            LocalGitWorkspace(root),
            base_commit=commit,
            requirement="update the readme",
            repository_url="https://github.com/acme/demo",
            default_branch="main",
        )

        assert "README.md" in context
        assert "repository note:" not in context
        assert "\ufffd" not in context


def test_strong_readiness_rejects_origin_or_branch_identity_mismatch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        project_id = uuid4()
        root = Path(temp_dir)
        project_root = root / str(project_id)
        project_root.mkdir()
        _git(project_root, "init", "-b", "main")
        _git(project_root, "config", "user.email", "devflow@example.invalid")
        _git(project_root, "config", "user.name", "DevFlow Test")
        (project_root / "README.md").write_text("ready\n", encoding="utf-8")
        _commit(project_root, "initial")
        _git(project_root, "remote", "add", "origin", "https://github.com/acme/demo.git")
        provisioner = ManagedProjectProvisioner(root)

        ready = provisioner.readiness(
            project_id,
            repository_url="https://github.com/acme/demo",
            default_branch="main",
        )
        wrong = provisioner.readiness(
            project_id,
            repository_url="https://github.com/acme/other",
            default_branch="main",
        )

        assert ready.ready is True
        assert wrong.ready is False


class _FakeRunStore:
    async def load_run(self, _run_id):
        return SimpleNamespace(
            project_id=uuid4(),
            repository_url="https://github.com/acme/demo",
            default_branch="main",
        )


class _RejectingProvisioner:
    def readiness(self, *_args, **_kwargs):
        return SimpleNamespace(ready=False, detail="identity mismatch")


class _UnexpectedWorker:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("worker must not execute after Project identity failure")


def test_worker_revalidates_project_identity_before_agent_execution() -> None:
    wrapper = ProjectIdentityValidatingQueuedTaskWorker(
        worker=_UnexpectedWorker(),  # type: ignore[arg-type]
        run_store=_FakeRunStore(),  # type: ignore[arg-type]
        provisioner=_RejectingProvisioner(),  # type: ignore[arg-type]
    )
    envelope = TaskDispatchEnvelope(
        dispatch_id=uuid4(),
        run_id=uuid4(),
        task_id="task-a",
    )

    with pytest.raises(WorkerExecutionBoundaryError):
        asyncio.run(wrapper.execute(envelope, run_token=uuid4()))


@pytest.mark.skipif(
    not os.getenv("DEVFLOW_DATABASE_URL"),
    reason="real PostgreSQL is required for Project lifecycle persistence",
)
def test_project_identity_allows_multiple_branches_and_canonical_retry() -> None:
    database_url = os.environ["DEVFLOW_DATABASE_URL"]

    async def scenario() -> None:
        store = ProjectAwarePostgresEvidenceStore.from_url(database_url)
        suffix = uuid4().hex
        try:
            first = await store.ensure_project(
                repository_url=f"https://github.com/acme/demo-{suffix}.git",
                default_branch="main",
            )
            retry = await store.ensure_project(
                repository_url=f"https://github.com/acme/demo-{suffix}/",
                default_branch="main",
            )
            develop = await store.ensure_project(
                repository_url=f"https://github.com/acme/demo-{suffix}",
                default_branch="develop",
            )
            assert retry == first
            assert develop != first
            await store.mark_project_failed(
                first,
                code="CLONE_FAILED",
                message="bounded failure",
            )
            await store.mark_project_provisioning(first)
        finally:
            await store.dispose()

    asyncio.run(scenario())
