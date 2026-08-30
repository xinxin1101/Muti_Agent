from __future__ import annotations

from app.models.workflow import WorkflowDefinition, WorkflowId


class WorkflowRegistry:
    """Static registry. Registration is deterministic and never contacts an LLM."""

    def __init__(self, definitions: tuple[WorkflowDefinition, ...]) -> None:
        if not definitions:
            raise ValueError("workflow registry requires at least one definition")
        workflow_ids = [item.workflow_id for item in definitions]
        if len(workflow_ids) != len(set(workflow_ids)):
            raise ValueError("workflow registry ids must be unique")
        self._definitions = definitions

    @property
    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        return self._definitions

    def get(self, workflow_id: WorkflowId) -> WorkflowDefinition:
        for definition in self._definitions:
            if definition.workflow_id is workflow_id:
                return definition
        raise KeyError(workflow_id)

    @classmethod
    def default(cls) -> WorkflowRegistry:
        return cls(
            definitions=(
                WorkflowDefinition(
                    workflow_id=WorkflowId.PYTHON_SCRIPT,
                    title="基础 Python 脚本",
                    description="创建、运行并验证单一 Python 脚本的确定性流程。",
                    deterministic_steps=("create_files", "run_python", "run_tests"),
                ),
                WorkflowDefinition(
                    workflow_id=WorkflowId.NODE_SCRIPT,
                    title="基础 Node 脚本",
                    description="创建、运行并验证单一 Node.js 脚本的确定性流程。",
                    deterministic_steps=("create_files", "run_node", "run_tests"),
                ),
                WorkflowDefinition(
                    workflow_id=WorkflowId.DEPENDENCY_PREFLIGHT,
                    title="依赖环境预检",
                    description="识别依赖、检查运行时并准备受控验证环境。",
                    deterministic_steps=("inspect_manifests", "check_runtime", "prepare_cache"),
                ),
                WorkflowDefinition(
                    workflow_id=WorkflowId.VERIFICATION,
                    title="确定性验证",
                    description="执行任务指定的测试、构建和静态检查命令。",
                    deterministic_steps=("run_checks", "collect_results"),
                ),
                WorkflowDefinition(
                    workflow_id=WorkflowId.GIT_PUBLICATION,
                    title="Git 发布",
                    description="创建 DevFlow 分支、提交变更并发布草稿 PR。",
                    deterministic_steps=("create_branch", "commit", "push", "create_draft_pr"),
                ),
            )
        )
