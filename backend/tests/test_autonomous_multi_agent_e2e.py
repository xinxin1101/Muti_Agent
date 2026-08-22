from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.api.autonomous import (
    AutonomousProductRuntimeService,
    RequirementRunCreateRequest,
    RequirementRunLaunchState,
)
from app.api.models import ProductDiffKind, ProductProject
from app.dispatch import DurableDramatiqTaskDispatcher
from app.models import (
    RunEvent,
    SingleTaskRunResult,
    TaskContract,
    TaskDAG,
    TaskNode,
    TaskRunState,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.publication import (
    GitHubPublicationSourceBasis,
    GitHubPublicationState,
    GitHubRemotePullRequest,
)
from app.persistence import (
    PersistenceEvidenceKind,
    PostgresDAGStore,
    PostgresDispatchAttemptStore,
    PostgresEvidenceStore,
    PostgresTaskLeaseStore,
    PostgresTaskReconciliationStore,
)
from app.persistence.publication import PostgresGitHubPublicationStore
from app.persistence.repair_completion import RepairAwarePostgresMultiTaskCompletionStore
from app.runtime.product_controller import DurableMultiAgentRunController
from app.runtime.reconciler import IdempotentTaskReconciler
from app.runtime.repair_execution_base import RepairAwareEvidenceBoundTaskExecutionBaseResolver
from app.runtime.run_reconciler import DAGRunReconciler
from app.workspace import LocalGitWorkspace


def _database_url() -> str:
    value = os.environ.get("DEVFLOW_DATABASE_URL", "").strip()
    if value:
        return value
    if os.environ.get("CI"):
        pytest.fail("CI must provide DEVFLOW_DATABASE_URL for autonomous E2E tests")
    pytest.skip("autonomous Multi-Agent E2E requires DEVFLOW_DATABASE_URL")


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "devflow-e2e@example.test")
    _git(root, "config", "user.name", "DevFlow E2E")
    (root / "README.md").write_text("# DevFlow E2E\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "baseline")
    return LocalGitWorkspace(root)


def _task(task_id: str, path: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Create {path} for the deterministic Multi-Agent acceptance run.",
        readable_files=["README.md", path],
        writable_files=[path],
        readonly_files=[],
        acceptance_criteria=[f"{path} exists with the accepted task output."],
        verification_commands=["git diff --check"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("root-a", "root_a.txt"), depends_on=()),
            TaskNode(task=_task("root-b", "root_b.txt"), depends_on=()),
            TaskNode(
                task=_task("dependent", "dependent.txt"),
                depends_on=("root-a", "root-b"),
            ),
        )
    )


class _FixedPlanner:
    def __init__(self, dag: TaskDAG) -> None:
        self._dag = dag
        self.requirement: str | None = None
        self.repository_context: str | None = None

    async def plan(
        self,
        requirement: str,
        *,
        repository_context: str | None = None,
    ) -> TaskDAG:
        self.requirement = requirement
        self.repository_context = repository_context
        return self._dag


class _Catalog:
    def __init__(self, project: ProductProject) -> None:
        self.project = project

    async def get_project(self, project_id: UUID) -> ProductProject:
        if project_id != self.project.project_id:
            raise ValueError("unknown project")
        return self.project

    async def list_projects(self, *, limit: int = 100):
        del limit
        return (self.project,)

    async def list_runs(self, *, project_id=None, limit: int = 100):
        del project_id, limit
        return ()

    async def dispose(self) -> None:
        return None


class _Provisioner:
    def is_ready(self, project_id: UUID) -> bool:
        del project_id
        return True


class _WorkspaceResolver:
    def __init__(self, project_id: UUID, workspace: LocalGitWorkspace) -> None:
        self._project_id = project_id
        self._workspace = workspace

    def resolve(self, project_id: UUID) -> LocalGitWorkspace:
        if project_id != self._project_id:
            raise ValueError("unknown project")
        return self._workspace


class _BrokerActor:
    queue_name = "devflow_tasks"

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def send(self, payload):
        self.payloads.append(payload)
        return SimpleNamespace(message_id=f"e2e-message-{len(self.payloads)}")

    def payload_for(self, task_id: str) -> dict:
        matches = [item for item in self.payloads if item["task_id"] == task_id]
        if len(matches) != 1:
            raise AssertionError(
                f"expected one broker payload for {task_id!r}; found {len(matches)}"
            )
        return matches[0]


class _Publisher:
    def __init__(self) -> None:
        self.intents = []

    async def publish(self, *, workspace, intent, title: str, body: str):
        del workspace, title, body
        self.intents.append(intent)
        return GitHubRemotePullRequest(
            number=101,
            html_url="https://github.com/example/devflow-e2e/pull/101",
            state="open",
            draft=True,
            head_branch=intent.branch_name,
            head_commit=intent.source_commit,
            base_branch=intent.base_branch,
        )

    async def dispose(self) -> None:
        return None


class _ControllerHandle:
    def __init__(self, controller: DurableMultiAgentRunController) -> None:
        self._controller = controller

    async def advance(self, run_id: UUID):
        return await self._controller.advance(run_id)

    async def dispose(self) -> None:
        return None


def _create_generation_worktree(
    workspace: LocalGitWorkspace,
    *,
    root: Path,
    task_id: str,
    branch_name: str,
    base_commit: str,
    path: str,
    content: str,
) -> tuple[Path, str]:
    worktree = root / task_id
    _git(
        workspace.root,
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree),
        base_commit,
    )
    (worktree / path).write_text(content, encoding="utf-8")
    _git(worktree, "add", "--", path)
    _git(worktree, "commit", "-m", f"Implement {task_id}")
    commit = _git(worktree, "rev-parse", "HEAD")
    assert _git(worktree, "rev-parse", "HEAD^") == base_commit
    return worktree, commit


def _run_result(task_id: str, changed_file: str) -> SingleTaskRunResult:
    return SingleTaskRunResult(
        task_id=task_id,
        status=TaskRunState.SUCCEEDED,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
            RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
        ],
        changed_files=[changed_file],
    )


async def _record_success(
    *,
    evidence_store: PostgresEvidenceStore,
    lease_store: PostgresTaskLeaseStore,
    actor: _BrokerActor,
    run_id: UUID,
    task_id: str,
    base_commit: str,
    branch_name: str,
    commit_sha: str,
    changed_file: str,
) -> WorkerExecutionEvidence:
    payload = actor.payload_for(task_id)
    dispatch_id = UUID(payload["dispatch_id"])
    grant = await lease_store.acquire_task_lease(
        run_id=run_id,
        task_id=task_id,
        owner_id=f"worker-{task_id}",
        dispatch_id=dispatch_id,
        lease_seconds=60,
    )
    execution = WorkerExecutionEvidence(
        dispatch_id=dispatch_id,
        run_id=run_id,
        task_id=task_id,
        status=WorkerExecutionStatus.SUCCEEDED,
        base_commit=base_commit,
        branch_name=branch_name,
        commit_sha=commit_sha,
        run_result=_run_result(task_id, changed_file),
        duration_ms=10,
    )
    await evidence_store.append_evidence(
        run_id=run_id,
        task_id=task_id,
        evidence_key=f"e2e:{dispatch_id}:worker-execution",
        kind=PersistenceEvidenceKind.WORKER_EXECUTION,
        payload_model=execution,
        stage="worker",
        run_token=grant.run_token,
    )
    await lease_store.release_task_lease(
        run_id=run_id,
        task_id=task_id,
        owner_id=f"worker-{task_id}",
        dispatch_id=dispatch_id,
        run_token=grant.run_token,
    )
    return execution


def test_requirement_to_draft_pr_runs_parallel_roots_then_dependency(
    tmp_path: Path,
) -> None:
    asyncio.run(_requirement_to_draft_pr_runs_parallel_roots_then_dependency(tmp_path))


async def _requirement_to_draft_pr_runs_parallel_roots_then_dependency(
    tmp_path: Path,
) -> None:
    database_url = _database_url()
    workspace = _repository(tmp_path)
    evidence_store = PostgresEvidenceStore.from_url(database_url)
    dag_store = PostgresDAGStore.from_url(database_url)
    dispatch_store = PostgresDispatchAttemptStore.from_url(database_url)
    lease_store = PostgresTaskLeaseStore.from_url(database_url)
    reconciliation_store = PostgresTaskReconciliationStore.from_url(database_url)
    completion_store = RepairAwarePostgresMultiTaskCompletionStore.from_url(database_url)
    publication_store = PostgresGitHubPublicationStore.from_url(database_url)
    actor = _BrokerActor()
    task_reconciler = IdempotentTaskReconciler(
        store=reconciliation_store,
        actor=actor,  # type: ignore[arg-type]
    )

    try:
        project_id = await evidence_store.ensure_project(
            repository_url="https://github.com/example/devflow-e2e.git",
            default_branch="main",
        )
        project = ProductProject(
            project_id=project_id,
            repository_url="https://github.com/example/devflow-e2e.git",
            default_branch="main",
            created_at=datetime.now(UTC),
            run_count=0,
            workspace_ready=True,
        )
        resolver = _WorkspaceResolver(project_id, workspace)
        planner = _FixedPlanner(_dag())
        dispatcher = DurableDramatiqTaskDispatcher(
            run_store=evidence_store,
            ledger=dispatch_store,
            actor=actor,  # type: ignore[arg-type]
        )
        execution_base_resolver = RepairAwareEvidenceBoundTaskExecutionBaseResolver(
            dag_reader=dag_store,
            workspace_resolver=resolver,
        )
        run_reconciler = DAGRunReconciler(
            run_reader=evidence_store,
            dag_reader=dag_store,
            lease_reader=lease_store,
            task_reconciler=task_reconciler,
            execution_base_resolver=execution_base_resolver,
        )
        controller = DurableMultiAgentRunController(
            evidence_store=evidence_store,
            dag_store=dag_store,
            workspace_resolver=resolver,
            run_reconciler=run_reconciler,
            completion_store=completion_store,
        )
        publisher = _Publisher()
        service = AutonomousProductRuntimeService(
            catalog=_Catalog(project),  # type: ignore[arg-type]
            evidence_store=evidence_store,
            dag_store=dag_store,
            provisioner=_Provisioner(),  # type: ignore[arg-type]
            workspace_resolver=resolver,
            dispatcher=dispatcher,
            publication_store=publication_store,
            github_publisher=publisher,
            requirement_planner=planner,
            run_controller=_ControllerHandle(controller),
        )

        requirement = (
            "Implement two independent root changes and then a dependent feature that uses "
            "their integrated result."
        )
        launch = await service.create_requirement_run(
            RequirementRunCreateRequest(
                project_id=project_id,
                requirement=requirement,
            )
        )

        assert launch.launch_state is RequirementRunLaunchState.QUEUED
        assert launch.task_ids == ("root-a", "root-b", "dependent")
        assert launch.initial_ready_task_ids == ("root-a", "root-b")
        assert [item["task_id"] for item in actor.payloads] == ["root-a", "root-b"]
        assert planner.requirement == requirement
        assert planner.repository_context is not None
        assert f"base_commit={launch.base_commit}" in planner.repository_context

        generation_root = tmp_path / "generations"
        generation_root.mkdir()
        _, root_a_commit = _create_generation_worktree(
            workspace,
            root=generation_root,
            task_id="root-a",
            branch_name="devflow/task/root-a",
            base_commit=launch.base_commit,
            path="root_a.txt",
            content="root-a\n",
        )
        _, root_b_commit = _create_generation_worktree(
            workspace,
            root=generation_root,
            task_id="root-b",
            branch_name="devflow/task/root-b",
            base_commit=launch.base_commit,
            path="root_b.txt",
            content="root-b\n",
        )
        await _record_success(
            evidence_store=evidence_store,
            lease_store=lease_store,
            actor=actor,
            run_id=launch.run_id,
            task_id="root-a",
            base_commit=launch.base_commit,
            branch_name="devflow/task/root-a",
            commit_sha=root_a_commit,
            changed_file="root_a.txt",
        )
        await _record_success(
            evidence_store=evidence_store,
            lease_store=lease_store,
            actor=actor,
            run_id=launch.run_id,
            task_id="root-b",
            base_commit=launch.base_commit,
            branch_name="devflow/task/root-b",
            commit_sha=root_b_commit,
            changed_file="root_b.txt",
        )

        roots_integrated = await controller.advance(launch.run_id)

        assert roots_integrated is not None
        assert roots_integrated.stopped is False
        assert roots_integrated.integrated_task_ids == ("root-a", "root-b")
        assert [item["task_id"] for item in actor.payloads] == [
            "root-a",
            "root-b",
            "dependent",
        ]
        dependent_attempts = await dispatch_store.list_for_task(
            run_id=launch.run_id,
            task_id="dependent",
        )
        assert len(dependent_attempts) == 1
        assert dependent_attempts[0].state.value == "ENQUEUED"

        dependent_base = roots_integrated.head_commit
        _, dependent_commit = _create_generation_worktree(
            workspace,
            root=generation_root,
            task_id="dependent",
            branch_name="devflow/task/dependent",
            base_commit=dependent_base,
            path="dependent.txt",
            content="depends-on-root-a-and-root-b\n",
        )
        await _record_success(
            evidence_store=evidence_store,
            lease_store=lease_store,
            actor=actor,
            run_id=launch.run_id,
            task_id="dependent",
            base_commit=dependent_base,
            branch_name="devflow/task/dependent",
            commit_sha=dependent_commit,
            changed_file="dependent.txt",
        )

        complete = await controller.advance(launch.run_id)

        assert complete is not None
        assert complete.stopped is False
        assert complete.integrated_task_ids == ("root-a", "root-b", "dependent")
        assert len(actor.payloads) == 3
        persisted = await evidence_store.load_run(launch.run_id)
        assert persisted.status.value == "SUCCEEDED"
        assert persisted.terminal_result is not None
        assert complete.head_commit != dependent_commit
        assert _git(workspace.root, "rev-parse", complete.integration_ref) == complete.head_commit

        run = await service.get_run(launch.run_id)
        assert run.status.value == "SUCCEEDED"
        assert run.task_count == 3

        diff = await service.get_task_diff(
            launch.run_id,
            "dependent",
            kind=ProductDiffKind.INTEGRATION,
        )
        assert diff.diff_kind is ProductDiffKind.INTEGRATION
        assert diff.base_commit == dependent_base
        assert diff.head_commit == complete.head_commit
        assert {item.path for item in diff.files} == {"dependent.txt"}

        ready = await service.get_github_publication(launch.run_id)
        assert ready.state is GitHubPublicationState.READY
        assert ready.source_basis is GitHubPublicationSourceBasis.INTEGRATION
        assert ready.source_commit == complete.head_commit

        published = await service.publish_github_draft(launch.run_id)

        assert published.state is GitHubPublicationState.PUBLISHED
        assert published.pull_request_number == 101
        assert published.pull_request_draft is True
        assert published.source_commit == complete.head_commit
        assert len(publisher.intents) == 1
        assert publisher.intents[0].source_commit == complete.head_commit
        assert publisher.intents[0].source_basis is GitHubPublicationSourceBasis.INTEGRATION
        after_publication = await evidence_store.load_run(launch.run_id)
        assert after_publication.status.value == "SUCCEEDED"
    finally:
        await task_reconciler.dispose()
        await completion_store.dispose()
        await publication_store.dispose()
        await lease_store.dispose()
        await dispatch_store.dispose()
        await dag_store.dispose()
        await evidence_store.dispose()
