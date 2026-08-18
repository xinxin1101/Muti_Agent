from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.models import RunEvent, SingleTaskRunResult, TaskContract, TaskRunState, WorkerExecutionStatus
from app.persistence import PostgresEvidenceStore, PostgresTaskLeaseStore
from app.workers.executor import LocalQueuedTaskExecutionBackend
from app.workspace import LocalGitWorkspace


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for run-token fencing tests")
    pytest.skip("run-token fencing test requires DEVFLOW_DATABASE_URL")


def _task() -> TaskContract:
    return TaskContract(
        task_id="FENCED-GIT",
        objective="Publish only the current execution generation.",
        readable_files=["module.py"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["Only the current run_token may publish Git output."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


class _StaticWorkspaceResolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self._workspace = workspace

    def resolve(self, _project_id):
        return self._workspace


class _WritingRunner:
    def __init__(self, value: int) -> None:
        self._value = value

    async def run(self, task: TaskContract, *, workspace: LocalGitWorkspace) -> SingleTaskRunResult:
        workspace.resolve_path("module.py").write_text(
            f"VALUE = {self._value}\n",
            encoding="utf-8",
        )
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.SUCCEEDED,
            events=[
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="Verified."),
            ],
            changed_files=["module.py"],
        )


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repository(path: Path) -> LocalGitWorkspace:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "devflow@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "DevFlow Test"],
        check=True,
    )
    (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "module.py"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    return LocalGitWorkspace(path)


def test_stale_generation_cannot_publish_git_but_takeover_generation_can(
    tmp_path: Path,
) -> None:
    asyncio.run(_stale_git_publication(tmp_path))


async def _stale_git_publication(tmp_path: Path) -> None:
    database_url = _database_url()
    base = _init_repository(tmp_path / "repo")
    task = _task()
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    project_id = await evidence_store.ensure_project(
        repository_url=f"https://example.test/{uuid4()}/fenced-git.git",
        default_branch="main",
    )
    base_commit = base.head_commit()
    run_id = await evidence_store.start_run(
        project_id=project_id,
        tasks=[task],
        base_commit=base_commit,
    )

    old_dispatch = uuid4()
    old_grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-old",
        dispatch_id=old_dispatch,
        lease_seconds=0.05,
    )
    await asyncio.sleep(0.08)
    new_dispatch = uuid4()
    new_grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task.task_id,
        owner_id="worker-new",
        dispatch_id=new_dispatch,
        lease_seconds=5.0,
    )
    assert new_grant.snapshot.generation == old_grant.snapshot.generation + 1

    stale_backend = LocalQueuedTaskExecutionBackend(
        workspace_resolver=_StaticWorkspaceResolver(base),
        worktree_root=tmp_path / "worktrees",
        runner_factory=lambda _task: _WritingRunner(2),
        publication_fence=lease_store,
    )
    stale = await stale_backend.execute(
        task=task,
        project_id=project_id,
        run_id=run_id,
        dispatch_id=old_dispatch,
        run_token=old_grant.run_token,
        base_commit=base_commit,
    )
    assert stale.status is WorkerExecutionStatus.FAILED
    assert stale.commit_sha is None
    assert stale.branch_name is not None
    assert stale.failures
    assert _git(base.root, "rev-parse", stale.branch_name) == base_commit

    current_backend = LocalQueuedTaskExecutionBackend(
        workspace_resolver=_StaticWorkspaceResolver(base),
        worktree_root=tmp_path / "worktrees",
        runner_factory=lambda _task: _WritingRunner(3),
        publication_fence=lease_store,
    )
    current = await current_backend.execute(
        task=task,
        project_id=project_id,
        run_id=run_id,
        dispatch_id=new_dispatch,
        run_token=new_grant.run_token,
        base_commit=base_commit,
    )
    assert current.status is WorkerExecutionStatus.SUCCEEDED
    assert current.commit_sha is not None
    assert current.branch_name is not None
    assert current.branch_name != stale.branch_name
    assert _git(base.root, "show", f"{current.commit_sha}:module.py") == "VALUE = 3"
    assert _git(base.root, "rev-parse", current.branch_name) == current.commit_sha

    await lease_store.dispose()
    await evidence_store.dispose()
