import asyncio
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.agent_runtime import build_repair_prefetch
from app.runtime import FailureClassifier
from app.tools import RepositoryToolbox
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self._responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeDriver received more calls than expected")
        return self._responses.pop(0)


def _response(
    content: str = "No patch produced.",
    *,
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    calls = tool_calls or []
    return models.AgentResponse(
        model="test/repair",
        content=content,
        tool_calls=calls,
        latency_ms=1,
        finish_reason="tool_calls" if calls else "stop",
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    src = root / "src"
    src.mkdir(parents=True)
    lines = [f"const marker{line} = {line};\n" for line in range(1, 161)]
    lines[107] = "const occupied = board[row][col]; // reviewer-target-line-108\n"
    (src / "gomoku_ui.js").write_text("".join(lines), encoding="utf-8")
    (src / "index.html").write_text(
        '<main id="gomoku-board"></main><script src="gomoku_ui.js"></script>\n',
        encoding="utf-8",
    )
    (root / "secret.txt").write_text("DO-NOT-PREFETCH\n", encoding="utf-8")
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="gomoku-ui",
        objective="Complete the browser-runnable Gomoku UI.",
        readable_files=["src/**"],
        writable_files=["src/index.html", "src/gomoku_ui.js"],
        acceptance_criteria=["The board prevents duplicate placement."],
        verification_commands=["python verify_gomoku.py"],
        max_retries=2,
    )


def _review_failure(*, file: str, line: int | None = 108) -> models.FailureReport:
    decision = models.ReviewDecision(
        decision=models.ReviewOutcome.CHANGES_REQUESTED,
        summary="The interaction semantics still need one targeted correction.",
        issues=[
            models.ReviewIssue(
                severity=models.ReviewSeverity.HIGH,
                message="Duplicate placement can overwrite an occupied cell.",
                file=file,
                line=line,
            )
        ],
    )
    reports = FailureClassifier.from_review(decision)
    assert len(reports) == 1
    return reports[0]


def _handoff(
    task: models.TaskContract,
    workspace: LocalGitWorkspace,
    failure: models.FailureReport,
) -> models.RepairHandoff:
    return models.RepairHandoff(
        task_id=task.task_id,
        objective=task.objective,
        repository_head=workspace.head_commit(),
        acceptance_criteria=tuple(task.acceptance_criteria),
        verification_commands=tuple(task.verification_commands),
        writable_files=tuple(task.writable_files),
        readonly_files=tuple(task.readonly_files),
        changed_files=("src/index.html", "src/gomoku_ui.js"),
        relevant_paths=("src/index.html",),
        failures=(
            models.RepairFailureDigest(
                failure_type=failure.failure_type,
                source=failure.source,
                message=failure.message,
                evidence=tuple(failure.evidence),
            ),
        ),
    )


def test_review_failure_derives_semantic_repair_target(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    handoff = _handoff(
        task,
        workspace,
        _review_failure(file="src/gomoku_ui.js", line=108),
    )

    assert handoff.failure_kind is models.RepairFailureKind.SEMANTIC_REVIEW_ISSUE
    assert handoff.suspected_path == "src/gomoku_ui.js"
    assert handoff.suspected_line == 108
    assert "src/gomoku_ui.js" in handoff.relevant_paths


def test_review_failure_without_line_still_targets_file(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    handoff = _handoff(
        _task(),
        workspace,
        _review_failure(file="src/gomoku_ui.js", line=None),
    )

    assert handoff.failure_kind is models.RepairFailureKind.SEMANTIC_REVIEW_ISSUE
    assert handoff.suspected_path == "src/gomoku_ui.js"
    assert handoff.suspected_line is None


def test_review_traversal_location_is_not_promoted_to_repair_target(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    handoff = _handoff(
        _task(),
        workspace,
        _review_failure(file="../secret.txt", line=1),
    )

    assert handoff.failure_kind is None
    assert handoff.suspected_path is None
    assert handoff.suspected_line is None
    assert "../secret.txt" not in handoff.relevant_paths


def test_semantic_review_prefetch_reads_bounded_window_around_line(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    handoff = _handoff(
        task,
        workspace,
        _review_failure(file="src/gomoku_ui.js", line=108),
    )
    toolbox = RepositoryToolbox(workspace=workspace, task=task, max_read_range_lines=120)

    prefetch = build_repair_prefetch(
        handoff,
        toolbox=toolbox,
        max_read_range_lines=120,
    )

    assert prefetch.performed is True
    assert prefetch.failure_kind is models.RepairFailureKind.SEMANTIC_REVIEW_ISSUE
    assert prefetch.path == "src/gomoku_ui.js"
    assert prefetch.line == 108
    assert "reviewer-target-line-108" in prefetch.source_preview
    assert "marker1 = 1" not in prefetch.source_preview
    assert "line=108" in prefetch.prompt_section()
    assert "Semantic review repair" in prefetch.prompt_section()


def test_semantic_review_prefetch_cannot_bypass_task_read_scope(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    handoff = _handoff(
        task,
        workspace,
        _review_failure(file="secret.txt", line=1),
    )
    toolbox = RepositoryToolbox(workspace=workspace, task=task, max_read_range_lines=120)

    prefetch = build_repair_prefetch(
        handoff,
        toolbox=toolbox,
        max_read_range_lines=120,
    )

    assert prefetch.performed is True
    assert prefetch.source_preview == ""
    assert prefetch.errors
    assert "DO-NOT-PREFETCH" not in prefetch.prompt_section()


def test_runtime_v3_first_repair_call_contains_review_target_and_source(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    failure = _review_failure(file="src/gomoku_ui.js", line=108)
    handoff = _handoff(task, workspace, failure)
    driver = FakeDriver([_response()])
    agent = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        max_iterations=1,
        runtime_v3_enabled=True,
        runtime_import_prefetch_enabled=True,
    )

    asyncio.run(
        agent.repair(
            task,
            [failure],
            attempt=1,
            workspace=workspace,
            handoff=handoff,
        )
    )

    assert len(driver.requests) == 1
    first_request = driver.requests[0]
    combined = "\n".join(message.content for message in first_request.messages)
    assert "SEMANTIC_REVIEW_ISSUE" in combined
    assert "path=src/gomoku_ui.js" in combined
    assert "line=108" in combined
    assert "reviewer-target-line-108" in combined


def test_semantic_prefetch_allows_one_observation_then_forces_mutation_only(
    tmp_path: Path,
) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    failure = _review_failure(file="src/gomoku_ui.js", line=108)
    handoff = _handoff(task, workspace, failure)
    observation = models.ToolCall(
        id="observe-target",
        name="read_range",
        arguments=json.dumps(
            {
                "path": "src/gomoku_ui.js",
                "start_line": 100,
                "end_line": 115,
            }
        ),
    )
    patch = models.ToolCall(
        id="repair-target",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "src/gomoku_ui.js",
                "old_text": (
                    "const occupied = board[row][col]; // reviewer-target-line-108"
                ),
                "new_text": (
                    "const occupied = board[row][col]; // reviewer-target-line-108\n"
                    "if (occupied) return;"
                ),
            }
        ),
    )
    driver = FakeDriver(
        [
            _response(content="", tool_calls=[observation]),
            _response(content="", tool_calls=[patch]),
            _response(content="unexpected extra model turn"),
        ]
    )
    agent = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        max_iterations=4,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=True,
    )

    result = asyncio.run(
        agent.repair(
            task,
            [failure],
            attempt=1,
            workspace=workspace,
            handoff=handoff,
        )
    )

    assert result.stop_reason is models.RepairStopReason.MODEL_STOP
    assert result.iterations == 2
    assert result.tool_calls == 2
    assert len(driver.requests) == 2
    assert {tool.name for tool in driver.requests[0].tools} > {
        "apply_patch",
        "write_file",
    }
    assert {tool.name for tool in driver.requests[1].tools} == {
        "apply_patch",
        "write_file",
    }
    second_prompt = "\n".join(message.content for message in driver.requests[1].messages)
    assert "MUTATION REQUIRED" in second_prompt
    assert "next turn is mutation-only" in second_prompt
    assert "src/gomoku_ui.js" in result.changed_files


def test_semantic_prefetch_hands_off_after_first_successful_mutation(
    tmp_path: Path,
) -> None:
    workspace = LocalGitWorkspace(_repository(tmp_path))
    task = _task()
    failure = _review_failure(file="src/gomoku_ui.js", line=108)
    handoff = _handoff(task, workspace, failure)
    patch = models.ToolCall(
        id="repair-immediately",
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "src/gomoku_ui.js",
                "old_text": (
                    "const occupied = board[row][col]; // reviewer-target-line-108"
                ),
                "new_text": (
                    "const occupied = board[row][col]; // reviewer-target-line-108\n"
                    "if (occupied) return;"
                ),
            }
        ),
    )
    driver = FakeDriver(
        [
            _response(content="", tool_calls=[patch]),
            _response(content="unexpected extra model turn"),
        ]
    )
    agent = agents.RepairAgent(
        driver=driver,
        model="test/repair",
        max_iterations=4,
        runtime_v3_enabled=True,
        runtime_mutation_gate_enabled=True,
        runtime_import_prefetch_enabled=True,
    )

    result = asyncio.run(
        agent.repair(
            task,
            [failure],
            attempt=1,
            workspace=workspace,
            handoff=handoff,
        )
    )

    assert result.stop_reason is models.RepairStopReason.MODEL_STOP
    assert result.iterations == 1
    assert result.tool_calls == 1
    assert len(driver.requests) == 1
    assert "src/gomoku_ui.js" in result.changed_files
