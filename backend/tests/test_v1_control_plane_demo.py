import asyncio
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.merge_queue import TopologicalMergeQueue
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.runtime.scheduler import DAGScheduler
from app.runtime.worker_coordinator import ParallelWorkerCoordinator
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace, TaskWorktreeManager


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self.responses = list(responses)

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        del request
        if not self.responses:
            raise AssertionError("required demo received an unexpected model call")
        return self.responses.pop(0)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(
            prompt_tokens=4,
            completion_tokens=2,
            total_tokens=6,
        ),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def test_required_demo_normal_success(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_value.py").write_text(
        "from module import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-demo@example.com")
    _git(root, "config", "user.name", "DevFlow Demo")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    patch = models.ToolCall(
        id="demo-write",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": "VALUE = 1",
                "new_text": "VALUE = 2",
            }
        ),
    )
    developer = FakeDriver(
        [
            _response(tool_calls=[patch]),
            _response(content="Implementation complete."),
        ]
    )
    reviewer = FakeDriver(
        [
            _response(
                content=models.ReviewDecision(
                    decision=models.ReviewOutcome.PASS,
                    summary="The verified implementation satisfies the task.",
                    issues=[],
                ).model_dump_json()
            )
        ]
    )
    repair = FakeDriver([])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer, model="fake/developer"),
        verifier=DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=reviewer, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=repair, model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )
    task = models.TaskContract(
        task_id="DEMO-NORMAL",
        objective="Change VALUE from 1 to 2 while preserving the protected test.",
        readable_files=["module.py", "tests/**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["module.py contains VALUE = 2 and the protected test passes."],
        verification_commands=["pytest -q"],
        max_retries=1,
    )

    result = asyncio.run(orchestrator.run(task, workspace=LocalGitWorkspace(root)))

    assert result.status is TaskRunState.SUCCEEDED, [
        failure.model_dump(mode="json") for failure in result.failures
    ]
    assert result.repair_attempts == 0
    assert len(result.verifications) == 1
    assert result.verifications[0].passed is True
    assert len(result.reviews) == 1
    assert result.reviews[0].decision is models.ReviewOutcome.PASS
    assert repair.responses == []


class _ParallelGate:
    def __init__(self, target: int = 2) -> None:
        self._target = target
        self.arrivals = 0
        self._release = asyncio.Event()

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals >= self._target:
            self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=3)


class _ConflictRunner:
    def __init__(self, gate: _ParallelGate) -> None:
        self._gate = gate

    async def run(
        self,
        task: models.TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> SingleTaskRunResult:
        workspace.resolve_path("shared.txt").write_text(
            f"VALUE={task.task_id}\n",
            encoding="utf-8",
        )
        await self._gate.wait()
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.SUCCEEDED,
            events=[
                RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Created."),
                RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Started."),
                RunEvent(sequence=2, state=TaskRunState.SUCCEEDED, detail="Succeeded."),
            ],
            changed_files=["shared.txt"],
        )


def test_required_demo_parallel_merge_conflict(tmp_path: Path) -> None:
    root = tmp_path / "parallel-repo"
    root.mkdir()
    (root / "shared.txt").write_text("VALUE=base\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-demo@example.com")
    _git(root, "config", "user.name", "DevFlow Demo")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")

    base = LocalGitWorkspace(root)
    tasks = tuple(
        models.TaskContract(
            task_id=task_id,
            objective=f"Independently update shared.txt for {task_id}.",
            readable_files=["shared.txt"],
            writable_files=["shared.txt"],
            readonly_files=[],
            acceptance_criteria=[f"shared.txt records {task_id}."],
            verification_commands=["git diff --check"],
            max_retries=1,
        )
        for task_id in ("TASK-A", "TASK-B")
    )
    dag = models.TaskDAG(tasks=tuple(models.TaskNode(task=task, depends_on=()) for task in tasks))
    scheduler = DAGScheduler(dag)
    worktrees = TaskWorktreeManager(base, tmp_path / "parallel-worktrees")
    gate = _ParallelGate()
    coordinator = ParallelWorkerCoordinator(
        scheduler=scheduler,
        worktrees=worktrees,
        runner_factory=lambda task: _ConflictRunner(gate),
        max_concurrency=2,
    )

    wave = asyncio.run(coordinator.run_ready_wave())

    assert gate.arrivals == 2
    assert wave.peak_concurrency == 2
    assert wave.scheduled_task_ids == ("TASK-A", "TASK-B")
    assert all(
        result.scheduler_state is models.TaskScheduleState.SUCCEEDED for result in wave.task_results
    )
    assert all(result.commit_sha is not None for result in wave.task_results)
    assert len({result.worktree_path for result in wave.task_results}) == 2
    assert (root / "shared.txt").read_text(encoding="utf-8") == "VALUE=base\n"

    queue = TopologicalMergeQueue(
        scheduler=scheduler,
        worktrees=worktrees,
        base_workspace=base,
        integration_id="demo-parallel-conflict",
    )
    snapshot = queue.integrate(list(wave.task_results))

    assert snapshot.stopped is True
    evidence = GitMergeConflictClassifier(base).classify(snapshot)
    assert evidence.conflicting_paths == ("shared.txt",)
    assert "CONFLICT (contents)" in evidence.conflict_types
    assert evidence.files[0].stage_shape is models.MergeConflictStageShape.THREE_WAY
    assert [stage.stage for stage in evidence.files[0].stages] == [1, 2, 3]
    assert base.changed_files() == []
