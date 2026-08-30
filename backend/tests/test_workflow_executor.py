from __future__ import annotations

import asyncio
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.models.dispatch import WorkerExecutionStatus
from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.models.verification import CheckResult, CheckType, VerificationResult
from app.models.workflow import WorkflowActivationMode, WorkflowExecutionMode, WorkflowId
from app.runtime.state_machine import TaskStateMachine
from app.workers.executor import LocalQueuedTaskExecutionBackend
from app.workflows import (
    DeterministicWorkflowRunner,
    WorkflowAwareTaskRunner,
    WorkflowMatcher,
    WorkflowRegistry,
)
from app.workspace import LocalGitWorkspace


def _workspace(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "workflow@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Workflow Test"],
        check=True,
    )
    (root / ".gitkeep").write_text("\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", ".gitkeep"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "baseline"], check=True)
    return LocalGitWorkspace(root)


def _task(
    *,
    task_id: str = "hello",
    node: bool = False,
    complex_task: bool = False,
) -> TaskContract:
    if complex_task:
        return TaskContract(
            task_id="gomoku",
            objective="实现五子棋 AI 和图形界面。",
            readable_files=["src/**"],
            writable_files=["game/core.py", "game/ui.py"],
            acceptance_criteria=["游戏逻辑与界面可用"],
            verification_commands=["pytest -q"],
        )
    suffix = "js" if node else "py"
    executable = "node" if node else "python"
    runtime = "Node.js" if node else "Python"
    return TaskContract(
        task_id=task_id,
        objective=f"用 {runtime} 编写 Hello World 程序，运行时打印 hello world。",
        readable_files=["src/**"],
        writable_files=[f"hello.{suffix}"],
        acceptance_criteria=["标准输出为 hello world。"],
        verification_commands=[f"{executable} hello.{suffix}"],
    )


class _TemplateVerifier:
    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.calls = 0

    def verify(self, task: TaskContract, *, workspace: LocalGitWorkspace) -> VerificationResult:
        self.calls += 1
        expected = (
            'console.log("hello world");\n'
            if task.writable_files[0].endswith(".js")
            else "print('hello world')\n"
        )
        source = workspace.resolve_path(task.writable_files[0]).read_text(encoding="utf-8")
        passed = self.passed and source == expected
        return VerificationResult(
            passed=passed,
            checks=[
                CheckResult(
                    check_type=CheckType.TEST,
                    name="template",
                    passed=passed,
                    failure_type=None if passed else FailureType.TEST_FAILURE,
                    stderr="" if passed else "template did not match",
                )
            ],
        )


class _SequenceTemplateVerifier(_TemplateVerifier):
    def __init__(self, outcomes: list[bool]) -> None:
        super().__init__()
        self._outcomes = iter(outcomes)

    def verify(self, task: TaskContract, *, workspace: LocalGitWorkspace) -> VerificationResult:
        self.passed = next(self._outcomes)
        return super().verify(task, workspace=workspace)


def _matcher() -> WorkflowMatcher:
    return WorkflowMatcher(WorkflowRegistry.default())


def test_python_hello_workflow_creates_and_verifies_without_agent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    runner = DeterministicWorkflowRunner(matcher=_matcher(), verifier=verifier)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert result.developer is None
    assert result.reviews == []
    assert verifier.calls == 1
    assert workspace.resolve_path("hello.py").read_text(encoding="utf-8") == (
        "print('hello world')\n"
    )
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.WORKFLOW
    assert result.workflow_execution.workflow_id is WorkflowId.PYTHON_SCRIPT


def test_node_hello_workflow_creates_node_script_without_agent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    runner = DeterministicWorkflowRunner(matcher=_matcher(), verifier=verifier)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_task(task_id="hello-node", node=True), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert verifier.calls == 1
    assert workspace.resolve_path("hello.js").read_text(encoding="utf-8") == (
        'console.log("hello world");\n'
    )


def test_workflow_retries_deterministically_without_entering_agent_repair(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier(passed=False)
    runner = DeterministicWorkflowRunner(matcher=_matcher(), verifier=verifier)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.FAILED
    assert result.developer is None
    assert result.repairs == []
    assert verifier.calls == 2
    assert result.workflow_execution is not None
    assert result.workflow_execution.attempts == 2


def test_workflow_escalates_to_agent_only_after_bounded_test_retries(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier(passed=False)
    factories = 0

    def agent_factory() -> _AgentRunner:
        nonlocal factories
        factories += 1
        return _AgentRunner()

    runner = WorkflowAwareTaskRunner(
        matcher=_matcher(),
        workflow_runner=DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=_matcher(), verifier=verifier
        ),
        agent_runner_factory=agent_factory,
    )

    result = asyncio.run(runner.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert verifier.calls == 2
    assert factories == 1
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.HYBRID
    assert result.workflow_execution.attempts == 2


class _AgentRunner:
    async def run(self, task: TaskContract, **_kwargs) -> SingleTaskRunResult:
        machine = TaskStateMachine()
        machine.transition(TaskRunState.RUNNING, detail="Agent started.")
        machine.transition(TaskRunState.VERIFYING, detail="Verification started.")
        machine.transition(TaskRunState.REVIEWING, detail="Review started.")
        machine.transition(TaskRunState.SUCCEEDED, detail="Agent succeeded.")
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=machine.state,
            events=machine.events,
        )


class _FailingAgentRunner:
    async def run(self, task: TaskContract, **_kwargs) -> SingleTaskRunResult:
        machine = TaskStateMachine()
        machine.transition(TaskRunState.RUNNING, detail="Agent started.")
        machine.transition(TaskRunState.FAILED, detail="Agent provider failed.")
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=machine.state,
            events=machine.events,
            failures=[
                FailureReport(
                    failure_type=FailureType.TOOL_FAILURE,
                    source=FailureSource.PROVIDER,
                    message="Provider failed during the Agent attempt.",
                    retryable=False,
                )
            ],
        )


def test_failed_agent_gets_one_final_deterministic_workflow_recovery(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _SequenceTemplateVerifier([False, False, True])
    factories = 0

    def agent_factory() -> _FailingAgentRunner:
        nonlocal factories
        factories += 1
        return _FailingAgentRunner()

    runner = WorkflowAwareTaskRunner(
        matcher=_matcher(),
        workflow_runner=DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=_matcher(), verifier=verifier
        ),
        agent_runner_factory=agent_factory,
    )

    result = asyncio.run(runner.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert factories == 1
    assert verifier.calls == 3
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.HYBRID
    assert "Developer Agent failed" in (result.workflow_execution.fallback_reason or "")


def test_complex_task_uses_agent_with_hybrid_workflow_record(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    factories = 0

    def agent_factory() -> _AgentRunner:
        nonlocal factories
        factories += 1
        return _AgentRunner()

    runner = WorkflowAwareTaskRunner(
        matcher=_matcher(),
        workflow_runner=DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=_matcher(), verifier=verifier
        ),
        agent_runner_factory=agent_factory,
    )

    result = asyncio.run(runner.run(_task(complex_task=True), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert factories == 1
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.HYBRID


def test_agent_only_policy_skips_an_eligible_workflow(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    factories = 0

    def agent_factory() -> _AgentRunner:
        nonlocal factories
        factories += 1
        return _AgentRunner()

    runner = WorkflowAwareTaskRunner(
        matcher=_matcher(),
        workflow_runner=DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=_matcher(), verifier=verifier
        ),
        agent_runner_factory=agent_factory,
        activation_mode=WorkflowActivationMode.AGENT_ONLY,
    )

    result = asyncio.run(runner.run(_task(), workspace=workspace))

    assert result.status is TaskRunState.SUCCEEDED
    assert factories == 1
    assert verifier.calls == 0
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.AGENT
    assert "agent_only" in (result.workflow_execution.fallback_reason or "")


def test_workflow_only_policy_rejects_complex_task_without_agent(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    factories = 0

    def agent_factory() -> _AgentRunner:
        nonlocal factories
        factories += 1
        return _AgentRunner()

    runner = WorkflowAwareTaskRunner(
        matcher=_matcher(),
        workflow_runner=DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=_matcher(), verifier=verifier
        ),
        agent_runner_factory=agent_factory,
        activation_mode=WorkflowActivationMode.WORKFLOW_ONLY,
    )

    result = asyncio.run(runner.run(_task(complex_task=True), workspace=workspace))

    assert result.status is TaskRunState.FAILED
    assert factories == 0
    assert verifier.calls == 0
    assert result.workflow_execution is not None
    assert result.workflow_execution.mode is WorkflowExecutionMode.HYBRID
    assert any("workflow_only" in item for item in result.failures[0].evidence)


class _WorkspaceResolver:
    def __init__(self, workspace: LocalGitWorkspace) -> None:
        self._workspace = workspace

    def resolve(self, _project_id: UUID) -> LocalGitWorkspace:
        return self._workspace


class _GitFence:
    @asynccontextmanager
    async def guard_task_git_mutation(self, **_kwargs):
        yield SimpleNamespace()


@pytest.mark.parametrize(
    ("node", "task_id", "source_file", "expected_source"),
    [
        (False, "hello-python", "hello.py", "print('hello world')\n"),
        (True, "hello-node", "hello.js", 'console.log("hello world");\n'),
    ],
)
def test_script_workflow_verifies_and_commits_through_queued_worker(
    tmp_path: Path,
    *,
    node: bool,
    task_id: str,
    source_file: str,
    expected_source: str,
) -> None:
    base = _workspace(tmp_path)
    verifier = _TemplateVerifier()
    matcher = _matcher()
    backend = LocalQueuedTaskExecutionBackend(
        workspace_resolver=_WorkspaceResolver(base),
        worktree_root=tmp_path / "worktrees",
        runner_factory=lambda _task: DeterministicWorkflowRunner(  # type: ignore[arg-type]
            matcher=matcher,
            verifier=verifier,
        ),
        git_fence=_GitFence(),
    )
    task = _task(task_id=task_id, node=node)

    evidence = asyncio.run(
        backend.execute(
            task=task,
            project_id=uuid4(),
            run_id=uuid4(),
            dispatch_id=uuid4(),
            run_token=uuid4(),
            base_commit=base.head_commit(),
        )
    )

    assert evidence.status is WorkerExecutionStatus.SUCCEEDED
    assert evidence.commit_sha is not None
    assert verifier.calls == 1
    committed = subprocess.run(
        ["git", "-C", str(base.root), "show", f"{evidence.commit_sha}:{source_file}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert committed.stdout == expected_source
