from __future__ import annotations

from app.models.task import TaskContract
from app.models.workflow import WorkflowExecutionMode, WorkflowId, WorkflowRoute
from app.workflows import WorkflowMatcher, WorkflowRegistry


def _task(
    *,
    task_id: str,
    objective: str,
    writable_files: list[str],
    verification_commands: list[str],
) -> TaskContract:
    return TaskContract(
        task_id=task_id,
        objective=objective,
        readable_files=["src/**"],
        writable_files=writable_files,
        readonly_files=[],
        acceptance_criteria=["任务能够通过指定验证命令。"],
        verification_commands=verification_commands,
        max_retries=1,
    )


def _matcher() -> WorkflowMatcher:
    return WorkflowMatcher(WorkflowRegistry.default())


def test_python_hello_world_is_matched_without_a_provider() -> None:
    match = _matcher().match_task(
        _task(
            task_id="hello-python",
            objective="用 Python 编写 Hello World 程序。",
            writable_files=["hello.py"],
            verification_commands=["python hello.py"],
        ),
        repository_files=("requirements.txt",),
    )

    assert match.route is WorkflowRoute.WORKFLOW_CANDIDATE
    assert match.workflow_id is WorkflowId.PYTHON_SCRIPT
    assert match.execution_mode is WorkflowExecutionMode.WORKFLOW
    assert match.confidence >= 0.85
    assert "verification_commands:python" in match.matched_rules


def test_basic_node_script_is_matched() -> None:
    match = _matcher().match_task(
        _task(
            task_id="hello-node",
            objective="编写一个 Node.js hello world 脚本。",
            writable_files=["hello.js"],
            verification_commands=["node hello.js"],
        ),
        repository_files=("package.json",),
    )

    assert match.route is WorkflowRoute.WORKFLOW_CANDIDATE
    assert match.workflow_id is WorkflowId.NODE_SCRIPT
    assert match.execution_mode is WorkflowExecutionMode.WORKFLOW
    assert WorkflowId.DEPENDENCY_PREFLIGHT in match.supporting_workflows


def test_complex_gomoku_task_falls_back_to_agent() -> None:
    match = _matcher().match_task(
        _task(
            task_id="gomoku",
            objective="使用 PySide6 实现五子棋游戏界面和 AI 对手。",
            writable_files=["game/core.py", "game/ui.py"],
            verification_commands=["pytest -q"],
        ),
        repository_files=("pyproject.toml",),
    )

    assert match.route is WorkflowRoute.AGENT_FALLBACK
    assert match.workflow_id is None
    assert match.execution_mode is WorkflowExecutionMode.HYBRID
    assert match.fallback_reason is not None


def test_mixed_python_and_node_task_falls_back_to_agent() -> None:
    match = _matcher().match_task(
        _task(
            task_id="mixed",
            objective="实现 Python 后端与 Node 工具。",
            writable_files=["api.py", "tool.js"],
            verification_commands=["python api.py", "node tool.js"],
        ),
        repository_files=("pyproject.toml", "package.json"),
    )

    assert match.route is WorkflowRoute.AGENT_FALLBACK
    assert "多模块" in (match.fallback_reason or "") or "Python 和 Node" in (
        match.fallback_reason or ""
    )
