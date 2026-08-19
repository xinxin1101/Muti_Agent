from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.api.publication import (
    ProductGitHubPublicationUnavailableError,
    resolve_github_publication_intent,
)
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.publication import (
    GitHubPublicationIntent,
    GitHubPublicationSourceBasis,
)
from app.models.task import TaskContract
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


def _task(task_id: str, path: str) -> PersistedTask:
    contract = TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}.",
        readable_files=["**/*"],
        writable_files=[path],
        readonly_files=[],
        acceptance_criteria=["Accepted by deterministic gates."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )
    return PersistedTask(
        task=contract,
        contract_sha256=("a" if task_id == "task-1" else "b") * 64,
        created_at=NOW,
    )


def _integration_repository(tmp_path: Path) -> tuple[Path, MergeQueueSnapshot]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "devflow@example.test")
    _git(root, "config", "user.name", "DevFlow Test")
    (root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    base = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-c", "agent/task-1", base)
    (root / "one.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "task one")
    task_one = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-c", "agent/task-2", base)
    (root / "two.txt").write_text("two\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "task two")
    task_two = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-c", "devflow/integration-test", base)
    _git(root, "merge", "--no-ff", "--no-edit", task_one)
    integration_one = _git(root, "rev-parse", "HEAD")
    _git(root, "merge", "--no-ff", "--no-edit", task_two)
    integration_two = _git(root, "rev-parse", "HEAD")

    snapshot = MergeQueueSnapshot(
        integration_ref="refs/heads/devflow/integration-test",
        run_base_commit=base,
        head_commit=integration_two,
        integrated_task_ids=("task-1", "task-2"),
        attempts=(
            MergeQueueAttempt(
                sequence=0,
                task_id="task-1",
                task_branch="agent/task-1",
                task_base_commit=base,
                task_commit=task_one,
                previous_integration_commit=base,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=integration_one,
            ),
            MergeQueueAttempt(
                sequence=1,
                task_id="task-2",
                task_branch="agent/task-2",
                task_base_commit=base,
                task_commit=task_two,
                previous_integration_commit=integration_one,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=integration_two,
            ),
        ),
    )
    return root, snapshot


def _multi_task_run(snapshot: MergeQueueSnapshot) -> PersistedRunSnapshot:
    return PersistedRunSnapshot(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        default_branch="main",
        base_commit=snapshot.run_base_commit,
        status=PersistedRunStatus.SUCCEEDED,
        tasks=(
            _task("task-1", "one.txt"),
            _task("task-2", "two.txt"),
        ),
        evidence=(
            PersistedEvidence(
                id=9,
                run_id=RUN_ID,
                task_id=None,
                evidence_key="merge:complete",
                kind=PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT,
                stage="integration",
                schema_version=1,
                payload=snapshot.model_dump(mode="json"),
                payload_sha256="c" * 64,
                created_at=NOW,
            ),
        ),
        terminal_result={"status": "SUCCEEDED"},
        terminal_result_sha256="d" * 64,
        started_at=NOW,
        finished_at=NOW,
    )


def test_multi_task_publication_requires_complete_reproducible_integration(
    tmp_path: Path,
) -> None:
    root, merge_snapshot = _integration_repository(tmp_path)
    intent = resolve_github_publication_intent(
        _multi_task_run(merge_snapshot),
        LocalGitWorkspace(root),
    )
    assert intent.source_basis is GitHubPublicationSourceBasis.INTEGRATION
    assert intent.source_commit == merge_snapshot.head_commit
    assert intent.source_evidence_id == 9

    incomplete = merge_snapshot.model_copy(
        update={
            "head_commit": merge_snapshot.attempts[0].integration_commit,
            "integrated_task_ids": ("task-1",),
            "attempts": (merge_snapshot.attempts[0],),
        }
    )
    with pytest.raises(ProductGitHubPublicationUnavailableError, match="complete"):
        resolve_github_publication_intent(
            _multi_task_run(incomplete),
            LocalGitWorkspace(root),
        )


def _intent() -> GitHubPublicationIntent:
    return GitHubPublicationIntent(
        run_id=RUN_ID,
        project_id=PROJECT_ID,
        repository_url="https://github.com/example/repo.git",
        repository_slug="example/repo",
        base_branch="main",
        branch_name=f"devflow/run-{RUN_ID}",
        source_basis=GitHubPublicationSourceBasis.SINGLE_TASK,
        source_commit="a" * 40,
        source_evidence_id=7,
        source_evidence_sha256="b" * 64,
    )


def _pr_payload(*, draft: bool = True, state: str = "open") -> dict[str, object]:
    intent = _intent()
    return {
        "number": 42,
        "html_url": "https://github.com/example/repo/pull/42",
        "state": state,
        "draft": draft,
        "head": {"ref": intent.branch_name, "sha": intent.source_commit},
        "base": {"ref": intent.base_branch},
    }


def test_github_rest_create_uses_explicit_version_and_exact_draft_target() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["headers"] = dict(request.headers)
        observed["body"] = json.loads(request.content)
        return httpx.Response(201, json=_pr_payload())

    async def exercise() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            gateway = GitHubPublicationGateway(
                SecretStr("super-secret-token"),
                client=client,
            )
            result = await gateway._create_pull_request(  # noqa: SLF001
                "example",
                "repo",
                _intent(),
                title="DevFlow test",
                body="bounded body",
            )
            assert result.number == 42

    asyncio.run(exercise())
    headers = observed["headers"]
    body = observed["body"]
    assert isinstance(headers, dict)
    assert headers["x-github-api-version"] == "2026-03-10"
    assert headers["authorization"] == "Bearer super-secret-token"
    assert isinstance(body, dict)
    assert body["draft"] is True
    assert body["head"] == f"devflow/run-{RUN_ID}"
    assert body["base"] == "main"
    assert body["maintainer_can_modify"] is False


def test_existing_matching_pr_must_still_be_open_draft() -> None:
    async def exercise(payload: dict[str, object]) -> str:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[payload]))
        async with httpx.AsyncClient(transport=transport) as client:
            gateway = GitHubPublicationGateway(SecretStr("token"), client=client)
            with pytest.raises(GitHubPublicationGatewayError) as raised:
                await gateway._find_pull_request("example", "repo", _intent())  # noqa: SLF001
            return raised.value.code

    assert asyncio.run(exercise(_pr_payload(draft=False))) == "REMOTE_PR_NOT_OPEN_DRAFT"
    assert asyncio.run(exercise(_pr_payload(state="closed"))) == "REMOTE_PR_NOT_OPEN_DRAFT"
