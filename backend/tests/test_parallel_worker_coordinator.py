from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest

from app import agents, models, workspace
from app.models.run import RunEvent, SingleTaskRunResult, TaskRunState
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.runtime.scheduler import DAGScheduler
from app.runtime.worker_coordinator import ParallelWorkerCoordinator
from app.verification import DeterministicVerifier


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


def _repository(tmp_path: Path) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    tests = root / "tests"
    tests.mkdir()
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    (root / "a.py").write_text("VALUE_A = 1\n", encoding="utf-8")
    (root / "b.py").write_text("VALUE_B = 1\n", encoding="utf-8")
    (tests / "test_a.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_a():\n"
        "    text = Path('a.py').read_text(encoding='utf-8')\n"
        "    assert 'VALUE_A = 2' in text\n",
        encoding="utf-8",
    )
    (tests / "test_b.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_b():\n"
        "    text = Path('b.py').read_text(encoding='utf-8')\n"
        "    assert 'VALUE_B = 2' in text\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def _task(
    task_id: str,
    *,
    writable: str = "shared.txt",
    verification_commands: list[str] | None = None,
) -> models.TaskContract:
    return models.TaskContract(
        task_id=task_id,
        objective=f"Complete {task_id} in its isolated worktree.",
        readable_files=["**"],
        writable_files=[writable],
        readonly_files=["tests/**"],
        acceptance_criteria=[f"{task_id} satisfies its isolated acceptance criteria"],
        verification_commands=verification_commands or ["pytest -q"],
        max_retries=1,
    )


def _dag(*nodes: tuple[models.TaskContract, tuple[str, ...]]) -> models.TaskDAG:
    return models.TaskDAG(
        tasks=tuple(models.TaskNode(task=task, depends_on=depends_on) for task, depends_on in nodes)
    )


def _terminal_result(
    task: models.TaskContract,
    *,
    succeeded: bool,
    changed_files: list[str],
) -> SingleTaskRunResult:
    status = TaskRunState.SUCCEEDED if succeeded else TaskRunState.FAILED
    failures: list[models.FailureReport] = []
    if not succeeded:
        failures.append(
            models.FailureReport(
                failure_type=models.FailureType.TEST_FAILURE,
                source=models.FailureSource.VERIFICATION,
                message="Synthetic worker test failure.",
                retryable=False,
                evidence=["synthetic=true"],
            )
        )
    return SingleTaskRunResult(
        task_id=task.task_id,
        status=status,
        events=[
            RunEvent(sequence=0, state=TaskRunState.PENDING, detail="Synthetic run created."),
            RunEvent(sequence=1, state=TaskRunState.RUNNING, detail="Synthetic run started."),
            RunEvent(sequence=2, state=status, detail="Synthetic run completed."),
        ],
        failures=failures,
        changed_files=changed_files,
    )


class AsyncGate:
    def __init__(self, target: int) -> None:
        self._target = target
        self.arrivals = 0
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def wait(self) -> None:
        self.arrivals += 1
        if self.arrivals >= self._target:
            self.reached.set()
        await self.release.wait()


class ControlledRunner:
    def __init__(
        self,
        *,
        gate: AsyncGate | None = None,
        succeeded: bool = True,
        raise_error: bool = False,
        cancel: bool = False,
    ) -> None:
        self._gate = gate
        self._succeeded = succeeded
        self._raise_error = raise_error
        self._cancel = cancel

    async def run(
        self,
        task: models.TaskContract,
        *,
        workspace: workspace.LocalGitWorkspace,
    ) -> SingleTaskRunResult:
        path = workspace.resolve_path("shared.txt")
        path.write_text(f"{task.task_id}\n", encoding="utf-8")
        if self._gate is not None:
            await self._gate.wait()
        if self._cancel:
            raise asyncio.CancelledError
        if self._raise_error:
            raise RuntimeError("synthetic worker crash")
        return _terminal_result(
            task,
            succeeded=self._succeeded,
            changed_files=["shared.txt"],
        )


class FakeDriver:
    def __init__(
        self,
        responses: list[models.AgentResponse],
        *,
        first_call_gate: AsyncGate | None = None,
    ) -> None:
        self._responses = list(responses)
        self._first_call_gate = first_call_gate
        self._calls = 0

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        del request
        self._calls += 1
        if self._calls == 1 and self._first_call_gate is not None:
            await self._first_call_gate.wait()
        if not self._responses:
            raise AssertionError("FakeDriver received an unexpected model call")
        return self._responses.pop(0)


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(prompt_tokens=2, completion_tokens=1, total_tokens=3),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _patch(call_id: str, *, path: str, old: str, new: str) -> models.ToolCall:
    return models.ToolCall(
        id=call_id,
        name="apply_patch",
        arguments=json.dumps({"path": path, "old_text": old, "new_text": new}),
    )


def _review_pass() -> str:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.PASS,
        summary="The isolated implementation satisfies the verified task contract.",
        issues=[],
    ).model_dump_json()


def _real_orchestrator(
    *,
    developer_driver: FakeDriver,
    reviewer_driver: FakeDriver,
    verifier=None,
) -> SingleTaskOrchestrator:
    return SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=verifier or DeterministicVerifier(command_timeout_seconds=10),
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=FakeDriver([]), model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
    )


def test_coordinator_rejects_unbounded_or_zero_concurrency(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    scheduler = DAGScheduler(_dag((_task("TASK-A"), ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    with pytest.raises(ValueError, match="at least one"):
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ControlledRunner(),
            max_concurrency=0,
        )


def test_bounded_wave_keeps_third_ready_until_a_worker_slot_is_free(tmp_path: Path) -> None:
    async def scenario() -> None:
        base = _repository(tmp_path)
        tasks = [_task("TASK-A"), _task("TASK-B"), _task("TASK-C")]
        scheduler = DAGScheduler(_dag(*((task, ()) for task in tasks)))
        manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
        gate = AsyncGate(target=2)
        coordinator = ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ControlledRunner(gate=gate),
            max_concurrency=2,
        )

        wave_task = asyncio.create_task(coordinator.run_ready_wave())
        await asyncio.wait_for(gate.reached.wait(), timeout=3)

        assert scheduler.task_ids_in_state(models.TaskScheduleState.RUNNING) == (
            "TASK-A",
            "TASK-B",
        )
        assert scheduler.state("TASK-C") is models.TaskScheduleState.READY

        gate.release.set()
        result = await asyncio.wait_for(wave_task, timeout=5)

        assert result.peak_concurrency == 2
        assert result.max_concurrency == 2
        assert result.scheduled_task_ids == ("TASK-A", "TASK-B", "TASK-C")
        assert all(
            item.scheduler_state is models.TaskScheduleState.SUCCEEDED
            for item in result.task_results
        )
        assert scheduler.is_terminal
        assert (base.root / "shared.txt").read_text(encoding="utf-8") == "base\n"

    asyncio.run(scenario())


def test_wave_does_not_auto_start_newly_ready_downstream_tasks(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    task_c = _task("TASK-C")
    scheduler = DAGScheduler(
        _dag(
            (task_a, ()),
            (task_b, ()),
            (task_c, ("TASK-A",)),
        )
    )
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    coordinator = ParallelWorkerCoordinator(
        scheduler=scheduler,
        worktrees=manager,
        runner_factory=lambda task: ControlledRunner(),
        max_concurrency=2,
    )

    result = asyncio.run(coordinator.run_ready_wave())

    assert result.scheduled_task_ids == ("TASK-A", "TASK-B")
    assert result.next_ready_task_ids == ("TASK-C",)
    assert scheduler.state("TASK-C") is models.TaskScheduleState.READY
    assert not manager.record_for("TASK-C").path.exists()


def test_failed_worker_blocks_only_its_dependency_branch(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    task_c = _task("TASK-C")
    task_d = _task("TASK-D")
    scheduler = DAGScheduler(
        _dag(
            (task_a, ()),
            (task_b, ()),
            (task_c, ("TASK-A",)),
            (task_d, ("TASK-B",)),
        )
    )
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    def factory(task: models.TaskContract) -> ControlledRunner:
        return ControlledRunner(succeeded=task.task_id != "TASK-A")

    result = asyncio.run(
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=factory,
            max_concurrency=2,
        ).run_ready_wave()
    )

    assert result.task_results[0].scheduler_state is models.TaskScheduleState.FAILED
    assert result.task_results[0].commit_sha is None
    assert result.task_results[1].scheduler_state is models.TaskScheduleState.SUCCEEDED
    assert result.task_results[1].commit_sha is not None
    assert scheduler.state("TASK-C") is models.TaskScheduleState.BLOCKED
    assert scheduler.state("TASK-D") is models.TaskScheduleState.READY
    assert result.next_ready_task_ids == ("TASK-D",)


def test_runner_exception_becomes_failed_worker_evidence(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag((task, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    result = asyncio.run(
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ControlledRunner(raise_error=True),
        ).run_ready_wave()
    )

    worker = result.task_results[0]
    assert worker.scheduler_state is models.TaskScheduleState.FAILED
    assert worker.run_result is None
    assert worker.failures[0].failure_type is models.FailureType.TOOL_FAILURE
    assert "exception_type=RuntimeError" in worker.failures[0].evidence
    assert scheduler.state("TASK-A") is models.TaskScheduleState.FAILED


def test_runner_cancellation_does_not_leave_task_running(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag((task, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    result = asyncio.run(
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ControlledRunner(cancel=True),
        ).run_ready_wave()
    )

    worker = result.task_results[0]
    assert worker.scheduler_state is models.TaskScheduleState.FAILED
    assert worker.failures[0].message == "Worker task was cancelled before terminal completion."
    assert scheduler.state("TASK-A") is models.TaskScheduleState.FAILED


def test_successful_worker_commits_only_its_task_branch(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    base_head = _git(base.root, "rev-parse", "HEAD")
    task = _task("TASK-A")
    scheduler = DAGScheduler(_dag((task, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    result = asyncio.run(
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=lambda task: ControlledRunner(),
        ).run_ready_wave()
    )

    worker = result.task_results[0]
    assert worker.commit_sha is not None
    assert _git(base.root, "rev-parse", "HEAD") == base_head
    assert _git(base.root, "rev-parse", worker.branch_name or "") == worker.commit_sha
    assert manager.open_workspace("TASK-A").changed_files() == []
    assert (base.root / "shared.txt").read_text(encoding="utf-8") == "base\n"


def test_two_real_v01_loops_run_in_isolated_worktrees_and_commit_independently(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        base = _repository(tmp_path)
        task_a = _task(
            "TASK-A",
            writable="a.py",
            verification_commands=["pytest -q tests/test_a.py", "ruff check ."],
        )
        task_b = _task(
            "TASK-B",
            writable="b.py",
            verification_commands=["pytest -q tests/test_b.py", "ruff check ."],
        )
        scheduler = DAGScheduler(_dag((task_a, ()), (task_b, ())))
        manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
        developer_gate = AsyncGate(target=2)

        def factory(task: models.TaskContract) -> SingleTaskOrchestrator:
            if task.task_id == "TASK-A":
                tool_call = _patch(
                    "dev-a",
                    path="a.py",
                    old="VALUE_A = 1",
                    new="VALUE_A = 2",
                )
            else:
                tool_call = _patch(
                    "dev-b",
                    path="b.py",
                    old="VALUE_B = 1",
                    new="VALUE_B = 2",
                )
            return _real_orchestrator(
                developer_driver=FakeDriver(
                    [
                        _response(tool_calls=[tool_call]),
                        _response(content="Initial isolated implementation completed."),
                    ],
                    first_call_gate=developer_gate,
                ),
                reviewer_driver=FakeDriver([_response(content=_review_pass())]),
            )

        wave_task = asyncio.create_task(
            ParallelWorkerCoordinator(
                scheduler=scheduler,
                worktrees=manager,
                runner_factory=factory,
                max_concurrency=2,
            ).run_ready_wave()
        )
        await asyncio.wait_for(developer_gate.reached.wait(), timeout=3)
        developer_gate.release.set()
        result = await asyncio.wait_for(wave_task, timeout=20)

        assert result.peak_concurrency == 2
        assert all(
            item.scheduler_state is models.TaskScheduleState.SUCCEEDED
            for item in result.task_results
        )
        assert all(item.commit_sha for item in result.task_results)
        assert all(item.run_result is not None for item in result.task_results)
        assert all(
            item.run_result is not None
            and item.run_result.verifications[-1].passed
            and item.run_result.reviews[-1].decision is models.ReviewOutcome.PASS
            for item in result.task_results
        )

        task_a_workspace = manager.open_workspace("TASK-A")
        task_b_workspace = manager.open_workspace("TASK-B")
        assert (task_a_workspace.root / "a.py").read_text(encoding="utf-8") == "VALUE_A = 2\n"
        assert (task_a_workspace.root / "b.py").read_text(encoding="utf-8") == "VALUE_B = 1\n"
        assert (task_b_workspace.root / "a.py").read_text(encoding="utf-8") == "VALUE_A = 1\n"
        assert (task_b_workspace.root / "b.py").read_text(encoding="utf-8") == "VALUE_B = 2\n"
        assert (base.root / "a.py").read_text(encoding="utf-8") == "VALUE_A = 1\n"
        assert (base.root / "b.py").read_text(encoding="utf-8") == "VALUE_B = 1\n"

    asyncio.run(scenario())


class ThreadBarrierVerifier:
    def __init__(self) -> None:
        self.barrier = threading.Barrier(2, timeout=3)
        self.thread_ids: set[int] = set()

    def verify(
        self,
        task: models.TaskContract,
        *,
        workspace: workspace.LocalGitWorkspace,
    ) -> models.VerificationResult:
        del task, workspace
        self.thread_ids.add(threading.get_ident())
        self.barrier.wait()
        return models.VerificationResult(
            passed=True,
            checks=[
                models.CheckResult(
                    check_type=models.CheckType.SCOPE,
                    name="synthetic concurrent verifier",
                    passed=True,
                )
            ],
        )


def test_v01_verifiers_are_offloaded_so_parallel_workers_do_not_block_event_loop(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    task_a = _task("TASK-A")
    task_b = _task("TASK-B")
    scheduler = DAGScheduler(_dag((task_a, ()), (task_b, ())))
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    verifier = ThreadBarrierVerifier()

    def factory(task: models.TaskContract) -> SingleTaskOrchestrator:
        return _real_orchestrator(
            developer_driver=FakeDriver(
                [
                    _response(
                        tool_calls=[
                            _patch(
                                f"dev-{task.task_id}",
                                path="shared.txt",
                                old="base",
                                new=task.task_id,
                            )
                        ]
                    ),
                    _response(content="Implementation completed."),
                ]
            ),
            reviewer_driver=FakeDriver([_response(content=_review_pass())]),
            verifier=verifier,
        )

    result = asyncio.run(
        ParallelWorkerCoordinator(
            scheduler=scheduler,
            worktrees=manager,
            runner_factory=factory,
            max_concurrency=2,
        ).run_ready_wave()
    )

    assert all(
        item.scheduler_state is models.TaskScheduleState.SUCCEEDED for item in result.task_results
    )
    assert len(verifier.thread_ids) == 2
