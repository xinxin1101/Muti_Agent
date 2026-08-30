from __future__ import annotations

import re
from collections.abc import Iterable

from app.models.task import TaskContract
from app.models.workflow import (
    WorkflowExecutionMode,
    WorkflowId,
    WorkflowMatch,
    WorkflowRoute,
)
from app.workflows.registry import WorkflowRegistry

_PYTHON_COMMAND = re.compile(r"^(python(?:3(?:\.\d+)?)?|pytest)\b", re.IGNORECASE)
_NODE_COMMAND = re.compile(r"^(node|npm|npx|pnpm|yarn)\b", re.IGNORECASE)
_PYTHON_WORD = re.compile(r"\bpython\b|\bhello\s*world\b|你好世界", re.IGNORECASE)
_NODE_WORD = re.compile(r"\b(node(?:\.js)?|javascript|typescript|npm)\b", re.IGNORECASE)
_COMPLEX_WORD = re.compile(
    r"\b(architecture|agent|ai|algorithm|database|api|frontend|backend|ui|game|pygame|tkinter|"
    r"react|vue|pyside|integration)\b|架构|智能体|算法|数据库|前端|后端|界面|游戏|集成",
    re.IGNORECASE,
)
_DEPENDENCY_WORD = re.compile(r"依赖|环境|预检|install|dependency|package", re.IGNORECASE)
_VERIFICATION_WORD = re.compile(r"测试|验证|检查|test|verify|lint|build", re.IGNORECASE)
_PUBLICATION_WORD = re.compile(r"github|git|分支|提交|推送|发布|pull request|pr", re.IGNORECASE)
_HELLO_WORLD_WORD = re.compile(r"\bhello[\s,，]*world\b|你好世界", re.IGNORECASE)


class WorkflowMatcher:
    """Classify narrowly bounded Tasks with transparent rules and no model calls."""

    def __init__(self, registry: WorkflowRegistry) -> None:
        self._registry = registry

    def match_task(
        self,
        task: TaskContract,
        *,
        repository_files: Iterable[str] = (),
    ) -> WorkflowMatch:
        repository_paths = tuple(path.lower() for path in repository_files)
        text = "\n".join(
            (
                task.objective,
                *task.acceptance_criteria,
                *task.verification_commands,
                *task.writable_files,
            )
        )
        supporting = self._supporting_workflows(text, repository_paths)

        if self._is_broad_or_complex(task, text):
            return self._fallback(
                task,
                supporting=supporting,
                reason="任务涉及多模块或开放式设计，需要 Agent 进行实现决策。",
            )

        python_rules = self._python_rules(task, text, repository_paths)
        node_rules = self._node_rules(task, text, repository_paths)
        if python_rules and node_rules:
            return self._fallback(
                task,
                supporting=supporting,
                reason="任务同时需要 Python 和 Node.js，无法安全选择单一基础脚本工作流。",
            )
        if python_rules:
            return self._candidate(task, WorkflowId.PYTHON_SCRIPT, python_rules, supporting)
        if node_rules:
            return self._candidate(task, WorkflowId.NODE_SCRIPT, node_rules, supporting)

        if _DEPENDENCY_WORD.search(text):
            return self._candidate(
                task,
                WorkflowId.DEPENDENCY_PREFLIGHT,
                ("objective:dependency-preflight",),
                supporting,
            )
        if _PUBLICATION_WORD.search(text):
            return self._candidate(
                task,
                WorkflowId.GIT_PUBLICATION,
                ("objective:git-publication",),
                supporting,
            )
        if _VERIFICATION_WORD.search(text):
            return self._candidate(
                task,
                WorkflowId.VERIFICATION,
                ("objective:verification",),
                supporting,
            )
        return self._fallback(
            task,
            supporting=supporting,
            reason="没有匹配到可安全自动执行的确定性工作流。",
        )

    def match_tasks(
        self,
        tasks: Iterable[TaskContract],
        *,
        repository_files: Iterable[str] = (),
    ) -> tuple[WorkflowMatch, ...]:
        paths = tuple(repository_files)
        return tuple(self.match_task(task, repository_files=paths) for task in tasks)

    def _candidate(
        self,
        task: TaskContract,
        workflow_id: WorkflowId,
        rules: tuple[str, ...],
        supporting: tuple[WorkflowId, ...],
    ) -> WorkflowMatch:
        self._registry.get(workflow_id)
        confidence = min(0.98, 0.70 + len(rules) * 0.09)
        return WorkflowMatch(
            task_id=task.task_id,
            route=WorkflowRoute.WORKFLOW_CANDIDATE,
            execution_mode=(
                WorkflowExecutionMode.WORKFLOW
                if workflow_id in {WorkflowId.PYTHON_SCRIPT, WorkflowId.NODE_SCRIPT}
                else WorkflowExecutionMode.HYBRID
            ),
            workflow_id=workflow_id,
            confidence=confidence,
            matched_rules=rules,
            supporting_workflows=supporting,
        )

    @staticmethod
    def _fallback(
        task: TaskContract,
        *,
        supporting: tuple[WorkflowId, ...],
        reason: str,
    ) -> WorkflowMatch:
        return WorkflowMatch(
            task_id=task.task_id,
            route=WorkflowRoute.AGENT_FALLBACK,
            execution_mode=(
                WorkflowExecutionMode.HYBRID if supporting else WorkflowExecutionMode.AGENT
            ),
            confidence=0.0,
            fallback_reason=reason,
            supporting_workflows=supporting,
        )

    @staticmethod
    def _python_rules(
        task: TaskContract, text: str, repository_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        rules: list[str] = []
        if any(path.endswith(".py") for path in task.writable_files):
            rules.append("writable_files:python")
        if any(_PYTHON_COMMAND.match(command.strip()) for command in task.verification_commands):
            rules.append("verification_commands:python")
        if _PYTHON_WORD.search(text) and _HELLO_WORLD_WORD.search(text):
            rules.append("objective:python-or-hello-world")
        if any(path in {"pyproject.toml", "requirements.txt"} for path in repository_paths):
            rules.append("repository:python-manifest")
        # First workflow version intentionally automates only a canonical, fully derivable
        # Hello World script. Other Python work remains Agent/HYBRID work rather than guessing.
        return tuple(rules) if len(rules) >= 2 and _HELLO_WORLD_WORD.search(text) else ()

    @staticmethod
    def _node_rules(
        task: TaskContract, text: str, repository_paths: tuple[str, ...]
    ) -> tuple[str, ...]:
        rules: list[str] = []
        if any(path.endswith((".js", ".mjs", ".cjs")) for path in task.writable_files):
            rules.append("writable_files:node")
        if any(_NODE_COMMAND.match(command.strip()) for command in task.verification_commands):
            rules.append("verification_commands:node")
        if _NODE_WORD.search(text) and _HELLO_WORLD_WORD.search(text):
            rules.append("objective:node")
        if "package.json" in repository_paths:
            rules.append("repository:package-json")
        return tuple(rules) if len(rules) >= 2 and _HELLO_WORLD_WORD.search(text) else ()

    @staticmethod
    def _supporting_workflows(
        text: str,
        repository_paths: tuple[str, ...],
    ) -> tuple[WorkflowId, ...]:
        workflows: list[WorkflowId] = [WorkflowId.VERIFICATION]
        if any(
            path in {"requirements.txt", "pyproject.toml", "package.json", "package-lock.json"}
            for path in repository_paths
        ) or _DEPENDENCY_WORD.search(text):
            workflows.insert(0, WorkflowId.DEPENDENCY_PREFLIGHT)
        if _PUBLICATION_WORD.search(text):
            workflows.append(WorkflowId.GIT_PUBLICATION)
        return tuple(workflows)

    @staticmethod
    def _is_broad_or_complex(task: TaskContract, text: str) -> bool:
        top_level_roots = {path.split("/", 1)[0] for path in task.writable_files}
        return (
            len(task.writable_files) > 2
            or len(top_level_roots) > 1
            or bool(_COMPLEX_WORD.search(text))
        )
