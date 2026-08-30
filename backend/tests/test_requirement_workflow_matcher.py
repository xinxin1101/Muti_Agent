from app.models.workflow import WorkflowExecutionMode
from app.workflows.requirement_matcher import RequirementWorkflowMatcher


def test_python_hello_world_creates_a_workflow_dag_without_a_planner() -> None:
    dag = RequirementWorkflowMatcher().match("用 Python 编写 hello world 程序")

    assert dag is not None
    node = dag.tasks[0]
    assert node.execution_mode is WorkflowExecutionMode.WORKFLOW
    assert node.task.task_id == "hello-world-python"
    assert node.task.writable_files == ["main.py"]
    assert node.task.verification_commands == ['test "$(python main.py)" = "hello world"']


def test_node_hello_world_creates_a_workflow_dag_without_a_planner() -> None:
    dag = RequirementWorkflowMatcher().match("请用 Node.js 写一个 Hello World")

    assert dag is not None
    assert dag.tasks[0].task.task_id == "hello-world-node"
    assert dag.tasks[0].execution_mode is WorkflowExecutionMode.WORKFLOW


def test_complex_gomoku_always_falls_back_to_planner() -> None:
    dag = RequirementWorkflowMatcher().match("用 Python 实现一个带 UI 和 AI 对手的五子棋游戏")

    assert dag is None


def test_ambiguous_hello_world_never_guesses_a_runtime() -> None:
    assert RequirementWorkflowMatcher().match("写一个 Hello World") is None
