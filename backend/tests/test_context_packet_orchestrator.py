import asyncio
import json
import subprocess
from pathlib import Path

from app import agents, models
from app.context import ContextPacketBuilder
from app.runtime.orchestrator import SingleTaskOrchestrator
from app.verification import (
    DeterministicVerifier,
    LocalProcessVerificationRunner,
)
from app.workspace import LocalGitWorkspace


class FakeDriver:
    def __init__(self, responses: list[models.AgentResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[models.AgentRequest] = []

    async def complete(self, request: models.AgentRequest) -> models.AgentResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("FakeDriver received an unexpected model call")
        return self.responses.pop(0)


class RecordingContextBuilder(ContextPacketBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.packets: list[models.ContextPacket] = []

    def build(
        self,
        task: models.TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> models.ContextPacket:
        packet = super().build(task, workspace=workspace)
        self.packets.append(packet)
        return packet


def _response(
    *,
    content: str = "",
    tool_calls: list[models.ToolCall] | None = None,
) -> models.AgentResponse:
    return models.AgentResponse(
        model="fake/model",
        content=content,
        tool_calls=tool_calls or [],
        usage=models.TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6),
        latency_ms=1,
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def _patch(call_id: str, old: str, new: str) -> models.ToolCall:
    return models.ToolCall(
        id=call_id,
        name="apply_patch",
        arguments=json.dumps(
            {
                "path": "module.py",
                "old_text": old,
                "new_text": new,
            }
        ),
    )


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository(tmp_path: Path) -> LocalGitWorkspace:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tests" / "test_value.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_value():\n"
        "    text = Path('module.py').read_text(encoding='utf-8')\n"
        "    assert 'VALUE = 2' in text\n",
        encoding="utf-8",
    )
    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow-context@example.com")
    _git(root, "config", "user.name", "DevFlow Context Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return LocalGitWorkspace(root)


def _task() -> models.TaskContract:
    return models.TaskContract(
        task_id="CTX-RUN-001",
        objective="Change VALUE to 2 while keeping tests protected.",
        readable_files=["**"],
        writable_files=["module.py"],
        readonly_files=["tests/**"],
        acceptance_criteria=["module.py contains VALUE = 2"],
        verification_commands=["pytest -q", "ruff check ."],
        max_retries=2,
    )


def _review_pass() -> str:
    return models.ReviewDecision(
        decision=models.ReviewOutcome.PASS,
        summary="The final implementation satisfies the task.",
        issues=[],
    ).model_dump_json()


def _selected_module_content(packet: models.ContextPacket) -> str:
    file = next(item for item in packet.selected_files if item.path == "module.py")
    return file.snippets[0].content


def test_orchestrator_rebuilds_context_from_current_worktree_for_each_agent_stage(
    tmp_path: Path,
) -> None:
    workspace = _repository(tmp_path)
    builder = RecordingContextBuilder()
    developer_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("dev-1", "VALUE = 1", "VALUE = 3")]),
            _response(content="Initial implementation finished."),
        ]
    )
    repair_driver = FakeDriver(
        [
            _response(tool_calls=[_patch("repair-1", "VALUE = 3", "VALUE = 2")]),
            _response(content="Targeted repair finished."),
        ]
    )
    reviewer_driver = FakeDriver([_response(content=_review_pass())])
    orchestrator = SingleTaskOrchestrator(
        developer=agents.DeveloperAgent(driver=developer_driver, model="fake/developer"),
        verifier=DeterministicVerifier(
            command_timeout_seconds=10,
            command_runner=LocalProcessVerificationRunner(),
        ),
        reviewer=agents.ReviewerAgent(driver=reviewer_driver, model="fake/reviewer"),
        repair=agents.RepairAgent(driver=repair_driver, model="fake/repair"),
        developer_model="fake/developer",
        reviewer_model="fake/reviewer",
        repair_model="fake/repair",
        context_builder=builder,
    )

    result = asyncio.run(orchestrator.run(_task(), workspace=workspace))

    assert result.status is models.TaskRunState.SUCCEEDED
    assert len(builder.packets) == 3
    assert [_selected_module_content(packet) for packet in builder.packets] == [
        "VALUE = 1\n",
        "VALUE = 3\n",
        "VALUE = 2\n",
    ]
    assert len({packet.fingerprint for packet in builder.packets}) == 3

    developer_prompt = developer_driver.requests[0].messages[-1].content
    repair_prompt = repair_driver.requests[0].messages[-1].content
    reviewer_prompt = reviewer_driver.requests[0].messages[-1].content
    assert "DeveloperContextView" in developer_prompt
    assert "VALUE = 1" in developer_prompt
    assert "RepairContextView" in repair_prompt
    assert "VALUE = 3" in repair_prompt
    assert "ReviewerContextView" in reviewer_prompt
    assert "VALUE = 2" in reviewer_prompt
    assert reviewer_driver.requests[0].tools == []
