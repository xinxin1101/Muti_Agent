from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.api.autonomous import (
    AutonomousProductRuntimeService,
    RequirementDispatchState,
    RequirementRunCreateRequest,
    RequirementRunLaunchState,
)
from app.api.models import ProductProject
from app.dispatch.errors import TaskDispatchBrokerError
from app.models.checkpoint import CheckpointReason, CheckpointResumeStrategy, TaskCheckpoint
from app.models.context import ContextContinuationState
from app.models.dag import TaskDAG, TaskNode
from app.models.development_session import (
    DevelopmentWorkPackageProgress,
    DevelopmentWorkPackageState,
)
from app.models.dispatch import TaskDispatchReceipt
from app.models.task import TaskContract
from app.models.work_package import TaskBudgetAllocation
from app.models.workflow import WorkflowActivationMode, WorkflowExecutionMode
from app.persistence.dag import PersistedDAGSnapshot, PersistedDAGSource
from app.verification.dependency_preflight import (
    DependencyEnvironmentPreflightError,
    DependencyPackageManager,
    DependencyPreflightFailureCode,
    DependencyPreflightReport,
)
from app.verification.project_profile import ProjectVerificationKind, VerificationRuntime


def _task(task_id: str, path: str) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=f"Implement {task_id}.",
        readable_files=["app/**"],
        writable_files=[path],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} is externally verifiable."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )


def _dag() -> TaskDAG:
    return TaskDAG(
        tasks=(
            TaskNode(task=_task("root-b", "app/b.py")),
            TaskNode(task=_task("dependent", "app/dependent.py"), depends_on=("root-a", "root-b")),
            TaskNode(task=_task("root-a", "app/a.py")),
        )
    )


class FakePlanner:
    def __init__(self, dag: TaskDAG) -> None:
        self.dag = dag
        self.requirement: str | None = None
        self.repository_context: str | None = None

    async def plan(self, requirement: str, *, repository_context: str | None = None) -> TaskDAG:
        self.requirement = requirement
        self.repository_context = repository_context
        return self.dag


class FakeCatalog:
    def __init__(self, project: ProductProject) -> None:
        self.project = project

    async def get_project(self, project_id: UUID) -> ProductProject:
        if project_id != self.project.project_id:
            raise ValueError("unknown project")
        return self.project

    async def list_projects(self, *, limit: int = 100):
        return (self.project,)

    async def list_runs(self, *, project_id=None, limit: int = 100):
        return ()

    async def dispose(self) -> None:
        return None


class FakeEvidenceStore:
    def __init__(self) -> None:
        self.workflow_matches = []

    async def append_evidence(self, *, kind, payload_model, **_kwargs) -> int:
        if kind.value == "WORKFLOW_MATCH":
            self.workflow_matches.append(payload_model)
        return len(self.workflow_matches)

    async def dispose(self) -> None:
        return None


class FakeDAGStore:
    def __init__(self) -> None:
        self.run_id = uuid4()
        self.started_dag: TaskDAG | None = None
        self.base_commit: str | None = None

    async def start_run(self, *, project_id, dag, base_commit, run_id=None):
        self.started_dag = dag
        self.base_commit = base_commit
        return self.run_id

    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot:
        assert run_id == self.run_id
        assert self.started_dag is not None
        return PersistedDAGSnapshot(
            run_id=run_id,
            dag=self.started_dag,
            dag_sha256="d" * 64,
            source=PersistedDAGSource.PERSISTED,
        )

    async def dispose(self) -> None:
        return None


class FakeProvisioner:
    def is_ready(self, project_id: UUID) -> bool:
        return True


class FakeWorkspace:
    root = Path(".")

    def head_commit(self) -> str:
        return "c" * 40

    def tracked_files(self) -> list[str]:
        return ["app/a.py", "app/b.py", "app/dependent.py", "tests/test_app.py"]


class FakeResolver:
    def resolve(self, project_id: UUID):
        return FakeWorkspace()


class FakeDispatcher:
    def __init__(self, *, fail_task_id: str | None = None) -> None:
        self.fail_task_id = fail_task_id
        self.dispatched: list[str] = []

    async def dispatch(self, *, run_id: UUID, task_id: str) -> TaskDispatchReceipt:
        self.dispatched.append(task_id)
        if task_id == self.fail_task_id:
            raise TaskDispatchBrokerError("broker unavailable")
        return TaskDispatchReceipt(
            dispatch_id=uuid4(),
            run_id=run_id,
            task_id=task_id,
            broker_message_id=f"message-{task_id}",
            queue_name="devflow_tasks",
        )


class FakePublicationStore:
    async def dispose(self) -> None:
        return None


class FailingDependencyPreflight:
    def check(self, _workspace: Path):
        raise DependencyEnvironmentPreflightError(
            code=DependencyPreflightFailureCode.REGISTRY_UNREACHABLE,
            package_manager=DependencyPackageManager.PYTHON,
            manifest_paths=("requirements.txt",),
            packages=("pygame",),
            reason="包源不可访问。",
        )


class RecordingDependencyPreflight:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def check(
        self,
        _workspace: Path,
        *,
        verification_commands: tuple[str, ...] = (),
    ) -> DependencyPreflightReport:
        self.commands.append(verification_commands)
        return DependencyPreflightReport(
            profile_kind=ProjectVerificationKind.PYTHON_BASE,
            dependency_fingerprint="a" * 64,
            package_manager=DependencyPackageManager.NONE,
            manifest_paths=(),
            packages=(),
            cache_state="NOT_REQUIRED",
            docker_version="28.3.2",
            proxy_configured=False,
            required_runtimes=(VerificationRuntime.PYTHON,),
        )


def _service(
    *,
    fail_task_id: str | None = None,
    dependency_preflight=None,
    workflow_activation_mode: WorkflowActivationMode = WorkflowActivationMode.WORKFLOW_FIRST,
    planner_configured: bool = True,
):
    project_id = uuid4()
    project = ProductProject(
        project_id=project_id,
        repository_url="https://github.com/example/repo",
        default_branch="main",
        created_at=datetime(2026, 8, 21, tzinfo=UTC),
        run_count=0,
        workspace_ready=True,
    )
    planner = FakePlanner(_dag()) if planner_configured else None
    dag_store = FakeDAGStore()
    dispatcher = FakeDispatcher(fail_task_id=fail_task_id)
    evidence_store = FakeEvidenceStore()
    service = AutonomousProductRuntimeService(
        catalog=FakeCatalog(project),  # type: ignore[arg-type]
        evidence_store=evidence_store,  # type: ignore[arg-type]
        dag_store=dag_store,  # type: ignore[arg-type]
        provisioner=FakeProvisioner(),  # type: ignore[arg-type]
        workspace_resolver=FakeResolver(),  # type: ignore[arg-type]
        dispatcher=dispatcher,  # type: ignore[arg-type]
        publication_store=FakePublicationStore(),  # type: ignore[arg-type]
        github_publisher=None,
        requirement_planner=planner,
        dependency_preflight=dependency_preflight,
        workflow_activation_mode=workflow_activation_mode,
    )
    return service, project_id, planner, dag_store, dispatcher, evidence_store


def test_python_hello_world_skips_planner_and_persists_workflow_dag() -> None:
    service, project_id, planner, dag_store, dispatcher, evidence_store = _service(
        planner_configured=False
    )

    result = asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(
                project_id=project_id,
                requirement="用 Python 编写 Hello World 程序",
            )
        )
    )

    assert planner is None
    assert dag_store.started_dag is not None
    assert dag_store.started_dag.tasks[0].execution_mode is WorkflowExecutionMode.WORKFLOW
    assert result.task_ids == ("hello-world-python",)
    assert dispatcher.dispatched == ["hello-world-python"]
    assert evidence_store.workflow_matches[0].workflow_id.value == "python-script"


def test_requirement_run_persists_validated_dag_before_dispatching_only_roots() -> None:
    service, project_id, planner, dag_store, dispatcher, evidence_store = _service()

    result = asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(
                project_id=project_id,
                requirement="Implement the feature as a multi-agent run.",
            )
        )
    )

    assert dag_store.started_dag is not None
    assert dag_store.started_dag.topological_order() == ["root-a", "root-b", "dependent"]
    assert dag_store.base_commit == "c" * 40
    assert dispatcher.dispatched == ["root-a", "root-b"]
    assert result.task_ids == ("root-a", "root-b", "dependent")
    assert result.initial_ready_task_ids == ("root-a", "root-b")
    assert result.launch_state is RequirementRunLaunchState.QUEUED
    assert all(item.state is RequirementDispatchState.QUEUED for item in result.dispatches)
    assert planner.requirement == "Implement the feature as a multi-agent run."
    assert planner.repository_context is not None
    assert "base_commit=" + "c" * 40 in planner.repository_context
    assert "app/dependent.py" in planner.repository_context
    assert {match.task_id for match in evidence_store.workflow_matches} == {
        "root-a",
        "root-b",
        "dependent",
    }


def test_requirement_run_reports_partial_broker_failure_without_dispatching_dependencies() -> None:
    service, project_id, _planner, _dag_store, dispatcher, _evidence_store = _service(
        fail_task_id="root-b"
    )

    result = asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(project_id=project_id, requirement="Implement feature.")
        )
    )

    assert dispatcher.dispatched == ["root-a", "root-b"]
    assert result.launch_state is RequirementRunLaunchState.PARTIAL
    failed = next(item for item in result.dispatches if item.task_id == "root-b")
    assert failed.state is RequirementDispatchState.BROKER_UNAVAILABLE
    assert failed.dispatch_id is None
    assert "dependent" not in dispatcher.dispatched


def test_requirement_run_stops_before_planning_when_dependency_preflight_fails() -> None:
    service, project_id, planner, dag_store, dispatcher, _evidence_store = _service(
        dependency_preflight=FailingDependencyPreflight()
    )

    with pytest.raises(DependencyEnvironmentPreflightError) as captured:
        asyncio.run(
            service.create_requirement_run(
                RequirementRunCreateRequest(project_id=project_id, requirement="实现一个游戏。")
            )
        )

    assert captured.value.code is DependencyPreflightFailureCode.REGISTRY_UNREACHABLE
    assert planner.requirement is None
    assert dag_store.started_dag is None
    assert dispatcher.dispatched == []


def test_requirement_run_checks_planned_node_commands_before_dispatch() -> None:
    preflight = RecordingDependencyPreflight()
    service, project_id, planner, _dag_store, dispatcher, _evidence_store = _service(
        dependency_preflight=preflight
    )
    node_task = _task("node-root", "app/game.js").model_copy(
        update={"verification_commands": ["node test/test_game.js"]}
    )
    planner.dag = TaskDAG(tasks=(TaskNode(task=node_task),))

    asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(project_id=project_id, requirement="实现 JavaScript 游戏。")
        )
    )

    assert preflight.commands == [(), ("node test/test_game.js",)]
    assert dispatcher.dispatched == ["node-root"]


def test_agent_only_rollout_policy_persists_agent_execution_modes() -> None:
    service, project_id, _planner, dag_store, _dispatcher, evidence_store = _service(
        workflow_activation_mode=WorkflowActivationMode.AGENT_ONLY
    )

    asyncio.run(
        service.create_requirement_run(
            RequirementRunCreateRequest(project_id=project_id, requirement="实现一个游戏。")
        )
    )

    assert dag_store.started_dag is not None
    assert all(
        node.execution_mode is WorkflowExecutionMode.AGENT for node in dag_store.started_dag.tasks
    )
    assert all(
        match.execution_mode is WorkflowExecutionMode.AGENT
        for match in evidence_store.workflow_matches
    )

def test_legacy_budget_checkpoint_with_verification_state_resumes_verifier_first() -> None:
    checkpoint = TaskCheckpoint(
        task_id="gomoku-core",
        base_commit="a" * 40,
        commit_sha="b" * 40,
        changed_files=("src/gomoku_engine.py",),
        reason=CheckpointReason.RUN_TOKEN_BUDGET_EXHAUSTED,
        summary="saved candidate",
        context_state=ContextContinuationState(
            summary_version="repository_summary_v1",
            repository_head="b" * 40,
            changed_files=("src/gomoku_engine.py",),
            verification_summary="已执行 1 次验证；最近结果=失败。",
            failure_summary="Deterministic custom verification failed.",
            remaining_summary="repair GameLogic import contract",
        ),
    )

    assert checkpoint.resume_strategy is None
    assert (
        AutonomousProductRuntimeService._checkpoint_resume_strategy(checkpoint)
        is CheckpointResumeStrategy.VERIFY_THEN_REPAIR
    )


def test_budget_checkpoint_without_verification_still_continues_developer() -> None:
    checkpoint = TaskCheckpoint(
        task_id="gomoku-core",
        base_commit="a" * 40,
        commit_sha="b" * 40,
        changed_files=("src/gomoku_engine.py",),
        reason=CheckpointReason.RUN_TOKEN_BUDGET_EXHAUSTED,
        summary="saved partial developer candidate",
        context_state=ContextContinuationState(
            summary_version="repository_summary_v1",
            repository_head="b" * 40,
            changed_files=("src/gomoku_engine.py",),
            remaining_summary="continue implementation",
        ),
    )

    assert (
        AutonomousProductRuntimeService._checkpoint_resume_strategy(checkpoint)
        is CheckpointResumeStrategy.CONTINUE_DEVELOPMENT
    )

def test_recovery_preview_credits_verified_checkpoint_development_reuse() -> None:
    service, _project_id, _planner, _dag_store, _dispatcher, _evidence_store = _service()
    core = TaskNode(
        task=_task("gomoku-core", "app/core.py"),
        budget_allocation=TaskBudgetAllocation(
            package_id="gomoku-core",
            recommended_token_budget=8_000,
        ),
    )
    ui = TaskNode(
        task=_task("gomoku-ui", "app/ui.py"),
        depends_on=("gomoku-core",),
        budget_allocation=TaskBudgetAllocation(
            package_id="gomoku-ui",
            recommended_token_budget=8_000,
        ),
    )
    session = SimpleNamespace(
        session_id=uuid4(),
        latest_run_id=None,
        planning_launch_id=None,
        base_commit="a" * 40,
        dag=TaskDAG(tasks=(core, ui)),
        work_packages=(
            DevelopmentWorkPackageProgress(
                task_id="gomoku-core",
                state=DevelopmentWorkPackageState.CHECKPOINTED,
                commit_sha="b" * 40,
                verification_summary="本切片已执行 1 次确定性验证。",
                context_state={
                    "verification_summary": "已执行 1 次验证；最近结果=失败。"
                },
            ),
            DevelopmentWorkPackageProgress(
                task_id="gomoku-ui",
                state=DevelopmentWorkPackageState.PENDING,
            ),
        ),
    )

    preview = asyncio.run(
        service._development_session_recovery_preview(
            session=session,
            current_commit="a" * 40,
        )
    )

    assert preview.checkpointed_work_package_ids == ("gomoku-core",)
    assert preview.remaining_work_package_ids == ("gomoku-core", "gomoku-ui")
    assert preview.budget.estimated_new_development_tokens == 8_000
    assert preview.budget.estimated_tokens_saved == 8_000
    assert preview.next_action == "从检查点继续；优先验证已保存代码"


def test_recovery_preview_does_not_credit_unverified_developer_checkpoint() -> None:
    service, _project_id, _planner, _dag_store, _dispatcher, _evidence_store = _service()
    node = TaskNode(
        task=_task("gomoku-core", "app/core.py"),
        budget_allocation=TaskBudgetAllocation(
            package_id="gomoku-core",
            recommended_token_budget=8_000,
        ),
    )
    session = SimpleNamespace(
        session_id=uuid4(),
        latest_run_id=None,
        planning_launch_id=None,
        base_commit="a" * 40,
        dag=TaskDAG(tasks=(node,)),
        work_packages=(
            DevelopmentWorkPackageProgress(
                task_id="gomoku-core",
                state=DevelopmentWorkPackageState.CHECKPOINTED,
                commit_sha="b" * 40,
                verification_summary="本切片尚未执行确定性验证。",
            ),
        ),
    )

    preview = asyncio.run(
        service._development_session_recovery_preview(
            session=session,
            current_commit="a" * 40,
        )
    )

    assert preview.budget.estimated_new_development_tokens == 8_000
    assert preview.budget.estimated_tokens_saved == 0
    assert preview.next_action == "继续未完成工作包"

