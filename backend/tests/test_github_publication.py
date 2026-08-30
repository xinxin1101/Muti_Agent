from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from app.api import create_app
from app.api.models import ProductGitHubPublication
from app.api.publication import (
    ProductGitHubPublicationUnavailableError,
    resolve_github_publication_intent,
)
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.publication import (
    GitHubPublicationIntent,
    GitHubPublicationSourceBasis,
    GitHubPublicationState,
    GitHubRemotePullRequest,
)
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.persistence import (
    PersistenceConflictError,
    PersistenceCorruptionError,
    PostgresEvidenceStore,
    PostgresGitHubPublicationStore,
)
from app.persistence.database import create_postgres_engine, create_session_factory
from app.persistence.models import GitHubPublicationRow, RunRow
from app.persistence.types import (
    PersistedEvidence,
    PersistedRunSnapshot,
    PersistedRunStatus,
    PersistedTask,
    PersistenceEvidenceKind,
)
from app.publication import GitHubPublicationGateway, GitHubPublicationGatewayError
from app.workspace import LocalGitWorkspace

RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "devflow@example.test")
    _git(root, "config", "user.name", "DevFlow Test")
    (root / "file.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "file.txt")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")
    (root / "file.txt").write_text("task\n", encoding="utf-8")
    _git(root, "add", "file.txt")
    _git(root, "commit", "-m", "task")
    return root, base, _git(root, "rev-parse", "HEAD")


def _task() -> TaskContract:
    return TaskContract(
        task_id="task-1",
        objective="Publish accepted code only.",
        readable_files=["**/*"],
        writable_files=["file.txt"],
        readonly_files=[],
        acceptance_criteria=["Publication does not decide success."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _worker_evidence(base: str, head: str) -> WorkerExecutionEvidence:
    return WorkerExecutionEvidence(
        dispatch_id=uuid4(),
        run_id=RUN_ID,
        task_id="task-1",
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base,
        branch_name="agent/task-1",
        commit_sha=head,
        run_result=SingleTaskRunResult(
            task_id="task-1",
            status=TaskRunState.SUCCEEDED,
            events=[
                RunEvent(sequence=0, state=TaskRunState.RUNNING, detail="started"),
                RunEvent(sequence=1, state=TaskRunState.SUCCEEDED, detail="accepted"),
            ],
        ),
        duration_ms=1,
    )


def _snapshot(base: str, head: str, *, status: PersistedRunStatus) -> PersistedRunSnapshot:
    evidence = _worker_evidence(base, head)
    terminal = status is not PersistedRunStatus.RUNNING
    return PersistedRunSnapshot(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        default_branch="main",
        base_commit=base,
        status=status,
        tasks=(PersistedTask(task=_task(), contract_sha256="a" * 64, created_at=NOW),),
        evidence=(
            PersistedEvidence(
                id=1,
                run_id=RUN_ID,
                task_id="task-1",
                evidence_key="worker:1",
                kind=PersistenceEvidenceKind.WORKER_EXECUTION,
                stage="worker",
                schema_version=1,
                payload=evidence.model_dump(mode="json"),
                payload_sha256="b" * 64,
                created_at=NOW,
            ),
        ),
        terminal_result={"status": status.value} if terminal else None,
        terminal_result_sha256="c" * 64 if terminal else None,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1) if terminal else None,
    )


def test_publication_requires_persisted_success_and_revalidates_git_parent(
    tmp_path: Path,
) -> None:
    root, base, head = _repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    with pytest.raises(ProductGitHubPublicationUnavailableError, match="SUCCEEDED"):
        resolve_github_publication_intent(
            _snapshot(base, head, status=PersistedRunStatus.RUNNING), workspace
        )

    intent = resolve_github_publication_intent(
        _snapshot(base, head, status=PersistedRunStatus.SUCCEEDED), workspace
    )
    assert intent.source_basis is GitHubPublicationSourceBasis.SINGLE_TASK
    assert intent.source_commit == head
    assert intent.branch_name == f"devflow/run-{RUN_ID}"

    with pytest.raises(PersistenceCorruptionError, match="accepted base"):
        resolve_github_publication_intent(
            _snapshot("d" * 40, head, status=PersistedRunStatus.SUCCEEDED), workspace
        )


def _intent(source_commit: str = "a" * 40) -> GitHubPublicationIntent:
    return GitHubPublicationIntent(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        repository_slug="example/repo",
        base_branch="main",
        branch_name=f"devflow/run-{RUN_ID}",
        source_basis=GitHubPublicationSourceBasis.SINGLE_TASK,
        source_commit=source_commit,
        source_evidence_id=1,
        source_evidence_sha256="b" * 64,
    )


def test_git_push_is_non_force_and_keeps_token_out_of_arguments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    source = "a" * 40
    lookup_count = 0

    def fake_run(command, **kwargs):
        nonlocal lookup_count
        calls.append((list(command), dict(kwargs["env"])))
        if "ls-remote" in command:
            lookup_count += 1
            requested_ref = command[-1]
            if requested_ref == "refs/heads/main":
                stdout = f"{source}\trefs/heads/main\n"
            else:
                stdout = "" if lookup_count == 2 else f"{source}\trefs/heads/devflow/run-{RUN_ID}\n"
        else:
            stdout = ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gateway = GitHubPublicationGateway(SecretStr("super-secret-token"))
    gateway._publish_branch(SimpleNamespace(root=tmp_path), _intent(source))  # noqa: SLF001

    push = next(command for command, _env in calls if "push" in command)
    assert "--force" not in push
    assert all("super-secret-token" not in part for part in push)
    assert f"{source}:refs/heads/devflow/run-{RUN_ID}" in push
    assert "refs/heads/main" not in push
    push_env = next(env for command, env in calls if "push" in command)
    assert "AUTHORIZATION: basic" in push_env["GIT_CONFIG_VALUE_0"]


def test_existing_devflow_branch_at_different_sha_fails_without_push(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        stdout = ""
        if "ls-remote" in command:
            requested_ref = command[-1]
            if requested_ref == "refs/heads/main":
                stdout = f"{'b' * 40}\trefs/heads/main\n"
            else:
                stdout = f"{'c' * 40}\trefs/heads/devflow/run-{RUN_ID}\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gateway = GitHubPublicationGateway(SecretStr("token"))
    with pytest.raises(GitHubPublicationGatewayError, match="different commit") as raised:
        gateway._publish_branch(SimpleNamespace(root=tmp_path), _intent())  # noqa: SLF001
    assert raised.value.code == "REMOTE_BRANCH_CONFLICT"
    assert not any("push" in command for command in commands)


def test_empty_remote_is_seeded_before_publishing_the_devflow_branch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = "a" * 40
    initial = "b" * 40
    commands: list[list[str]] = []
    base_lookup_count = 0
    branch_lookup_count = 0

    def fake_run(command, **kwargs):
        nonlocal base_lookup_count, branch_lookup_count
        commands.append(list(command))
        stdout = ""
        if "ls-remote" in command:
            requested_ref = command[-1]
            if requested_ref == "refs/heads/main":
                base_lookup_count += 1
                if base_lookup_count > 1:
                    stdout = f"{initial}\trefs/heads/main\n"
            else:
                branch_lookup_count += 1
                if branch_lookup_count > 1:
                    stdout = f"{source}\trefs/heads/devflow/run-{RUN_ID}\n"
        elif "rev-list" in command:
            stdout = f"{initial}\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gateway = GitHubPublicationGateway(SecretStr("token"))
    gateway._publish_branch(SimpleNamespace(root=tmp_path), _intent(source))  # noqa: SLF001

    pushes = [command for command in commands if "push" in command]
    assert f"{initial}:refs/heads/main" in pushes[0]
    assert f"{source}:refs/heads/devflow/run-{RUN_ID}" in pushes[1]


class FakePublicationApi:
    def __init__(self) -> None:
        self.publish_calls = 0

    async def get_github_publication(self, run_id: UUID) -> ProductGitHubPublication:
        assert run_id == RUN_ID
        return _product_publication()

    async def publish_github_draft(self, run_id: UUID) -> ProductGitHubPublication:
        assert run_id == RUN_ID
        self.publish_calls += 1
        return _product_publication()


def _product_publication() -> ProductGitHubPublication:
    return ProductGitHubPublication(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        state=GitHubPublicationState.READY,
        source_basis=GitHubPublicationSourceBasis.SINGLE_TASK,
        source_commit="a" * 40,
        source_evidence_id=1,
        source_evidence_sha256="b" * 64,
        repository_slug="example/repo",
        base_branch="main",
        branch_name=f"devflow/run-{RUN_ID}",
        publisher_configured=True,
        attempt_count=0,
    )


async def _request(method: str, path: str, service: FakePublicationApi, *, content=None):
    transport = httpx.ASGITransport(app=create_app(service))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, content=content)


def test_publication_api_rejects_browser_body_and_query_selectors() -> None:
    service = FakePublicationApi()
    body = asyncio.run(
        _request(
            "POST",
            f"/api/v1/runs/{RUN_ID}/github-publication",
            service,
            content=b'{"source_commit":"' + b"d" * 40 + b'"}',
        )
    )
    assert body.status_code == 400
    query = asyncio.run(
        _request(
            "POST",
            f"/api/v1/runs/{RUN_ID}/github-publication?ref=main",
            service,
        )
    )
    assert query.status_code == 400
    assert service.publish_calls == 0

    accepted = asyncio.run(_request("POST", f"/api/v1/runs/{RUN_ID}/github-publication", service))
    assert accepted.status_code == 200
    assert service.publish_calls == 1


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for publication persistence tests")
    pytest.skip("PostgreSQL publication persistence test requires DEVFLOW_DATABASE_URL")


def test_publication_claim_fences_concurrent_and_stale_attempts() -> None:
    asyncio.run(_publication_claim_fences_concurrent_and_stale_attempts())


async def _publication_claim_fences_concurrent_and_stale_attempts() -> None:
    database_url = _database_url()
    evidence = PostgresEvidenceStore.from_url(database_url)
    publication = PostgresGitHubPublicationStore.from_url(database_url)
    repository_name = str(uuid4())
    repository_url = f"https://github.com/example/{repository_name}.git"
    project_id = await evidence.ensure_project(
        repository_url=repository_url,
        default_branch="main",
    )
    run_id = await evidence.start_run(
        project_id=project_id,
        tasks=[_task()],
        base_commit="a" * 40,
    )

    engine = create_postgres_engine(database_url)
    sessions = create_session_factory(engine)
    async with sessions.begin() as session:
        await session.execute(
            update(RunRow)
            .where(RunRow.id == run_id)
            .values(status=PersistedRunStatus.SUCCEEDED.value)
        )

    intent = _intent().model_copy(
        update={
            "run_id": run_id,
            "project_id": project_id,
            "repository_url": repository_url,
            "repository_slug": f"example/{repository_name}",
            "branch_name": f"devflow/run-{run_id}",
        }
    )
    attempt, token_one = await publication.begin_attempt(intent)
    assert attempt.state is GitHubPublicationState.PUBLISHING
    assert attempt.attempt_count == 1
    assert token_one is not None

    with pytest.raises(PersistenceConflictError, match="already in progress"):
        await publication.begin_attempt(intent)

    async with sessions.begin() as session:
        await session.execute(
            update(GitHubPublicationRow)
            .where(GitHubPublicationRow.run_id == run_id)
            .values(attempt_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
        )

    takeover, token_two = await publication.begin_attempt(intent)
    assert takeover.state is GitHubPublicationState.PUBLISHING
    assert takeover.attempt_count == 2
    assert token_two is not None and token_two != token_one

    with pytest.raises(PersistenceConflictError, match="stale or expired"):
        await publication.mark_failed(
            run_id=run_id,
            intent_sha256=takeover.intent_sha256,
            attempt_token=token_one,
            error_code="STALE",
            error_message="must not overwrite takeover",
        )

    remote = GitHubRemotePullRequest(
        number=7,
        html_url=f"https://github.com/{intent.repository_slug}/pull/7",
        state="open",
        draft=True,
        head_branch=intent.branch_name,
        head_commit=intent.source_commit,
        base_branch=intent.base_branch,
    )
    published = await publication.mark_published(
        run_id=run_id,
        intent_sha256=takeover.intent_sha256,
        attempt_token=token_two,
        remote=remote,
    )
    assert published.state is GitHubPublicationState.PUBLISHED

    after_failure = await publication.mark_failed(
        run_id=run_id,
        intent_sha256=takeover.intent_sha256,
        attempt_token=token_two,
        error_code="LATE_FAILURE",
        error_message="must not downgrade",
    )
    assert after_failure.state is GitHubPublicationState.PUBLISHED
    assert after_failure.pull_request_number == 7

    await engine.dispose()
    await evidence.dispose()
    await publication.dispose()
