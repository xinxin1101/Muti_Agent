from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.dag import TaskDAG, TaskNode
from app.models.task import TaskContract
from app.models.workflow import WorkflowExecutionMode

_HELLO_WORLD = re.compile(r"\bhello[\s,，]*world\b|你好世界", re.IGNORECASE)
_PYTHON = re.compile(r"\bpython(?:3)?\b|蟒蛇", re.IGNORECASE)
_NODE = re.compile(r"\bnode(?:\.js)?\b|javascript|js 脚本", re.IGNORECASE)
_COMPLEX = re.compile(
    r"\b(game|ui|ai|database|frontend|backend|api|agent|algorithm|react|vue|pygame|"
    r"tkinter|pyside|multi[- ]?module)\b|游戏|界面|人工智能|数据库|前端|后端|接口|智能体|"
    r"算法|多模块",
    re.IGNORECASE,
)


class RequirementWorkflowMatcher:
    """Create only fully derivable template DAGs before any Planner provider call.

    A miss is deliberately cheap and safe: it means the ordinary Planner path remains the sole
    authority for interpreting an open-ended requirement.
    """

    def match(
        self,
        requirement: str,
        *,
        repository_files: Iterable[str] = (),
    ) -> TaskDAG | None:
        text = requirement.strip()
        if not _HELLO_WORLD.search(text) or _COMPLEX.search(text):
            return None
        paths = {path.lower() for path in repository_files}
        # Avoid guessing a language when the request is ambiguous or a repository declares the
        # opposite runtime as its primary dependency profile.
        python_requested = bool(_PYTHON.search(text))
        node_requested = bool(_NODE.search(text))
        if python_requested == node_requested:
            return None
        if python_requested and "package.json" in paths:
            return None
        if node_requested and any(path in paths for path in {"pyproject.toml", "requirements.txt"}):
            return None
        if python_requested:
            return self._python_hello_world()
        return self._node_hello_world()

    @staticmethod
    def _python_hello_world() -> TaskDAG:
        return TaskDAG(
            tasks=(
                TaskNode(
                    task=TaskContract(
                        task_id="hello-world-python",
                        objective="用 Python 编写一个 Hello World 程序。",
                        readable_files=[],
                        writable_files=["main.py"],
                        readonly_files=[],
                        acceptance_criteria=['程序在标准输出打印 "hello world"。'],
                        verification_commands=['test "$(python main.py)" = "hello world"'],
                        max_retries=0,
                    ),
                    execution_mode=WorkflowExecutionMode.WORKFLOW,
                ),
            )
        )

    @staticmethod
    def _node_hello_world() -> TaskDAG:
        return TaskDAG(
            tasks=(
                TaskNode(
                    task=TaskContract(
                        task_id="hello-world-node",
                        objective="用 Node.js 编写一个 Hello World 程序。",
                        readable_files=[],
                        writable_files=["index.js"],
                        readonly_files=[],
                        acceptance_criteria=['程序在标准输出打印 "hello world"。'],
                        verification_commands=["node index.js"],
                        max_retries=0,
                    ),
                    execution_mode=WorkflowExecutionMode.WORKFLOW,
                ),
            )
        )
