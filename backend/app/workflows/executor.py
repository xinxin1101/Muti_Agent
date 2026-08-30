from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

from app.models.context import ContextContinuationState
from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.models.workflow import (
    WorkflowActivationMode,
    WorkflowExecutionMode,
    WorkflowExecutionRecord,
    WorkflowId,
    WorkflowMatch,
    WorkflowRoute,
    WorkflowStepResult,
    WorkflowStepStatus,
)
from app.runtime.state_machine import TaskStateMachine
from app.trace.collector import TaskTraceCollector
from app.verification import DeterministicVerifier
from app.workflows.matcher import WorkflowMatcher
from app.workspace import LocalGitWorkspace

_HELLO_OUTPUT = re.compile(r"\bhello(?:[\s,，]+)world!?", re.IGNORECASE)


class DeterministicWorkflowRunner:
    """Execute strictly derivable script workflows without an LLM.

    The runner deliberately supports only the narrow templates selected by WorkflowMatcher. It is
    idempotent: a resumed attempt rewrites the same scoped source and repeats the same verifier.
    """

    def __init__(
        self,
        *,
        matcher: WorkflowMatcher,
        verifier: DeterministicVerifier,
        max_attempts: int = 2,
        estimated_tokens_saved: int = 0,
    ) -> None:
        if not 1 <= max_attempts <= 3:
            raise ValueError("workflow max_attempts must be between 1 and 3")
        self._matcher = matcher
        self._verifier = verifier
        self._max_attempts = max_attempts
        self._estimated_tokens_saved = max(0, estimated_tokens_saved)

    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        trace: TaskTraceCollector | None = None,
        continuation_context: ContextContinuationState | None = None,
    ) -> SingleTaskRunResult:
        del continuation_context  # The file template is idempotent; no model context is needed.
        match = self._matcher.match_task(task, repository_files=workspace.repository_files())
        if (
            match.route is not WorkflowRoute.WORKFLOW_CANDIDATE
            or match.execution_mode is not WorkflowExecutionMode.WORKFLOW
            or match.workflow_id not in {WorkflowId.PYTHON_SCRIPT, WorkflowId.NODE_SCRIPT}
        ):
            return self._unsupported_result(task, match)

        machine = TaskStateMachine()
        machine.transition(TaskRunState.RUNNING, detail="Deterministic Workflow started.")
        steps: list[WorkflowStepResult] = []
        verifications = []
        for attempt in range(1, self._max_attempts + 1):
            try:
                detail = await asyncio.to_thread(
                    self._write_template,
                    task,
                    workspace,
                    match.workflow_id,
                )
            except (OSError, ValueError) as exc:
                steps.append(
                    WorkflowStepResult(
                        name="create_files",
                        status=WorkflowStepStatus.FAILED,
                        detail=str(exc),
                        attempt=attempt,
                    )
                )
                machine.transition(
                    TaskRunState.FAILED,
                    detail="Workflow could not create template.",
                )
                return self._result(
                    task,
                    machine,
                    workflow_id=match.workflow_id,
                    attempts=attempt,
                    steps=steps,
                    failures=[self._template_failure(exc)],
                    verifications=verifications,
                    estimated_tokens_saved=0,
                )

            steps.append(
                WorkflowStepResult(
                    name="create_files",
                    status=WorkflowStepStatus.SUCCEEDED,
                    detail=detail,
                    attempt=attempt,
                )
            )
            machine.transition(
                TaskRunState.VERIFYING,
                detail=f"Workflow deterministic verification started (attempt {attempt}).",
            )
            verification = await asyncio.to_thread(self._verifier.verify, task, workspace=workspace)
            verifications.append(verification)
            if verification.passed:
                steps.append(
                    WorkflowStepResult(
                        name="verification",
                        status=WorkflowStepStatus.SUCCEEDED,
                        detail="All task verification commands passed.",
                        attempt=attempt,
                    )
                )
                # No semantic LLM review is necessary for a template with one fixed behaviour.
                machine.transition(
                    TaskRunState.REVIEWING,
                    detail="Workflow template contract checked; LLM review was not required.",
                )
                machine.transition(
                    TaskRunState.SUCCEEDED,
                    detail="Deterministic Workflow completed.",
                )
                return self._result(
                    task,
                    machine,
                    workflow_id=match.workflow_id,
                    attempts=attempt,
                    steps=steps,
                    failures=[],
                    verifications=verifications,
                    estimated_tokens_saved=self._estimated_tokens_saved,
                )

            steps.append(
                WorkflowStepResult(
                    name="verification",
                    status=WorkflowStepStatus.FAILED,
                    detail="Verification failed; Workflow will retry its deterministic template.",
                    attempt=attempt,
                )
            )
            if attempt < self._max_attempts:
                # VERIFYING -> REPAIRING is an explicit deterministic retry, not an Agent loop.
                machine.transition(
                    TaskRunState.REPAIRING,
                    detail=f"Workflow deterministic retry {attempt + 1} scheduled.",
                )

        machine.transition(TaskRunState.FAILED, detail="Workflow exhausted deterministic retries.")
        return self._result(
            task,
            machine,
            workflow_id=match.workflow_id,
            attempts=self._max_attempts,
            steps=steps,
            failures=DeterministicVerifier.failure_reports(verifications[-1])
            or [
                FailureReport(
                    failure_type=FailureType.TOOL_FAILURE,
                    source=FailureSource.RUNTIME,
                    message=(
                        "Workflow deterministic verification failed without diagnostic evidence."
                    ),
                    retryable=False,
                )
            ],
            verifications=verifications,
            estimated_tokens_saved=0,
        )

    @staticmethod
    def _write_template(
        task: TaskContract,
        workspace: LocalGitWorkspace,
        workflow_id: WorkflowId,
    ) -> str:
        extensions = {WorkflowId.PYTHON_SCRIPT: ".py", WorkflowId.NODE_SCRIPT: ".js"}
        candidates = [
            path for path in task.writable_files if path.endswith(extensions[workflow_id])
        ]
        if len(candidates) != 1:
            raise ValueError("Workflow requires exactly one writable source file for its template.")
        output = DeterministicWorkflowRunner._expected_output(task)
        if output is None:
            raise ValueError("Workflow cannot derive the required program output from this task.")
        source = (
            f"print({output!r})\n"
            if workflow_id is WorkflowId.PYTHON_SCRIPT
            else f"console.log({json.dumps(output, ensure_ascii=False)});\n"
        )
        target = workspace.resolve_path(candidates[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = target.read_text(encoding="utf-8") if target.exists() else None
        target.write_text(source, encoding="utf-8")
        action = "reused" if previous == source else "created"
        return f"Workflow {action} {candidates[0]} with a deterministic Hello World template."

    @staticmethod
    def _expected_output(task: TaskContract) -> str | None:
        # Acceptance criteria are the authoritative observable contract when they specify an
        # output, so prefer them over a title-style objective such as "Hello World".
        text = "\n".join((*task.acceptance_criteria, task.objective))
        match = _HELLO_OUTPUT.search(text)
        if match is None:
            return None
        return match.group(0)

    @staticmethod
    def _template_failure(exc: Exception) -> FailureReport:
        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message="Workflow 无法从任务契约安全生成确定性代码。",
            retryable=False,
            evidence=[f"exception_type={type(exc).__name__}", str(exc)[:512]],
        )

    @staticmethod
    def _result(
        task: TaskContract,
        machine: TaskStateMachine,
        *,
        workflow_id: WorkflowId,
        attempts: int,
        steps: list[WorkflowStepResult],
        failures: list[FailureReport],
        verifications: list,
        estimated_tokens_saved: int,
    ) -> SingleTaskRunResult:
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=machine.state,
            events=machine.events,
            verifications=verifications,
            failures=failures,
            changed_files=[],
            workflow_execution=WorkflowExecutionRecord(
                task_id=task.task_id,
                mode=WorkflowExecutionMode.WORKFLOW,
                workflow_id=workflow_id,
                attempts=attempts,
                steps=tuple(steps),
                estimated_tokens_saved=estimated_tokens_saved,
            ),
        ).model_copy(update={"changed_files": []})

    def _unsupported_result(self, task: TaskContract, match: WorkflowMatch) -> SingleTaskRunResult:
        machine = TaskStateMachine()
        machine.transition(
            TaskRunState.FAILED,
            detail="Workflow route is not executable by this runner.",
        )
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.FAILED,
            events=machine.events,
            failures=[
                FailureReport(
                    failure_type=FailureType.TOOL_FAILURE,
                    source=FailureSource.RUNTIME,
                    message="Workflow route is not executable; Agent fallback is required.",
                    retryable=False,
                    evidence=[f"workflow_route={match.route.value}"],
                )
            ],
            workflow_execution=WorkflowExecutionRecord(
                task_id=task.task_id,
                mode=WorkflowExecutionMode.HYBRID,
                workflow_id=match.workflow_id,
                fallback_reason=(
                    match.fallback_reason or "Workflow executor does not support route."
                ),
            ),
        )


class WorkflowAwareTaskRunner:
    """Select a zero-token Workflow or lazily construct the Agent runtime for a task."""

    def __init__(
        self,
        *,
        matcher: WorkflowMatcher,
        workflow_runner: DeterministicWorkflowRunner,
        agent_runner_factory: Callable[[], object],
        activation_mode: WorkflowActivationMode = WorkflowActivationMode.WORKFLOW_FIRST,
    ) -> None:
        self._matcher = matcher
        self._workflow_runner = workflow_runner
        self._agent_runner_factory = agent_runner_factory
        self._activation_mode = activation_mode

    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        trace: TaskTraceCollector | None = None,
        continuation_context: ContextContinuationState | None = None,
    ) -> SingleTaskRunResult:
        match = self._matcher.match_task(task, repository_files=workspace.repository_files())
        if self._activation_mode is WorkflowActivationMode.AGENT_ONLY:
            return await self._agent_result(
                task,
                match=match,
                workspace=workspace,
                trace=trace,
                continuation_context=continuation_context,
                mode=WorkflowExecutionMode.AGENT,
                fallback_reason="当前运行策略为 agent_only，已跳过确定性 Workflow。",
            )
        if (
            self._activation_mode is WorkflowActivationMode.WORKFLOW_ONLY
            and match.execution_mode is not WorkflowExecutionMode.WORKFLOW
        ):
            return self._workflow_only_result(task, match)
        if match.execution_mode is WorkflowExecutionMode.WORKFLOW:
            workflow_result = await self._workflow_runner.run(
                task,
                workspace=workspace,
                trace=trace,
                continuation_context=continuation_context,
            )
            if (
                workflow_result.status is TaskRunState.SUCCEEDED
                or self._activation_mode is WorkflowActivationMode.WORKFLOW_ONLY
                or not self._requires_agent_escalation(workflow_result)
            ):
                return workflow_result
            # A bounded template retry already happened. Escalate only a code/test failure to
            # the Agent; environment, scope and tool failures are deterministic operational
            # faults that an Agent cannot repair.
            agent_result = await self._run_agent(
                task,
                workspace=workspace,
                trace=trace,
                continuation_context=continuation_context,
            )
            prior = workflow_result.workflow_execution
            escalation_reason = (
                "Workflow deterministic retries exhausted a code verification failure; "
                "escalated once to the Developer Agent."
            )
            if agent_result.status is TaskRunState.SUCCEEDED:
                return agent_result.model_copy(
                    update={
                        "workflow_execution": WorkflowExecutionRecord(
                            task_id=task.task_id,
                            mode=WorkflowExecutionMode.HYBRID,
                            workflow_id=match.workflow_id,
                            attempts=prior.attempts if prior is not None else 0,
                            steps=prior.steps if prior is not None else (),
                            fallback_reason=escalation_reason,
                        )
                    }
                )

            # A transient Agent/provider failure must not discard a deterministic route that
            # already proved it can safely build this narrow template. One final bounded retry
            # lets the Workflow recover a simple task without opening another Agent loop.
            recovery_result = await self._workflow_runner.run(
                task,
                workspace=workspace,
                trace=trace,
                continuation_context=continuation_context,
            )
            recovery = recovery_result.workflow_execution
            recovery_reason = (
                f"{escalation_reason} Developer Agent failed; the deterministic Workflow "
                "was retried once as the final recovery path."
            )
            if recovery_result.status is TaskRunState.SUCCEEDED:
                return recovery_result.model_copy(
                    update={
                        "developer": agent_result.developer,
                        "repairs": agent_result.repairs,
                        "repair_attempts": agent_result.repair_attempts,
                        "agent_models": agent_result.agent_models,
                        "agent_usage": agent_result.agent_usage,
                        "workflow_execution": WorkflowExecutionRecord(
                            task_id=task.task_id,
                            mode=WorkflowExecutionMode.HYBRID,
                            workflow_id=match.workflow_id,
                            attempts=recovery.attempts if recovery is not None else 0,
                            steps=recovery.steps if recovery is not None else (),
                            fallback_reason=recovery_reason,
                        ),
                    }
                )
            return agent_result.model_copy(
                update={
                    "workflow_execution": WorkflowExecutionRecord(
                        task_id=task.task_id,
                        mode=WorkflowExecutionMode.HYBRID,
                        workflow_id=match.workflow_id,
                        attempts=recovery.attempts if recovery is not None else 0,
                        steps=recovery.steps if recovery is not None else (),
                        fallback_reason=recovery_reason,
                    )
                }
            )

        # Complex tasks retain Agent code generation, while verification, environment preparation
        # and fenced branch/commit publication stay in the deterministic surrounding runtime.
        return await self._agent_result(
            task,
            match=match,
            workspace=workspace,
            trace=trace,
            continuation_context=continuation_context,
            mode=match.execution_mode,
            fallback_reason=match.fallback_reason,
        )

    async def _agent_result(
        self,
        task: TaskContract,
        *,
        match: WorkflowMatch,
        workspace: LocalGitWorkspace,
        trace: TaskTraceCollector | None,
        continuation_context: ContextContinuationState | None,
        mode: WorkflowExecutionMode,
        fallback_reason: str | None,
    ) -> SingleTaskRunResult:
        result = await self._run_agent(
            task,
            workspace=workspace,
            trace=trace,
            continuation_context=continuation_context,
        )
        return result.model_copy(
            update={
                "workflow_execution": WorkflowExecutionRecord(
                    task_id=task.task_id,
                    mode=mode,
                    workflow_id=(
                        match.workflow_id if mode is not WorkflowExecutionMode.AGENT else None
                    ),
                    fallback_reason=fallback_reason,
                )
            }
        )

    @staticmethod
    def _workflow_only_result(task: TaskContract, match: WorkflowMatch) -> SingleTaskRunResult:
        machine = TaskStateMachine()
        machine.transition(
            TaskRunState.FAILED,
            detail="Workflow-only policy rejected a task without a supported deterministic route.",
        )
        return SingleTaskRunResult(
            task_id=task.task_id,
            status=TaskRunState.FAILED,
            events=machine.events,
            failures=[
                FailureReport(
                    failure_type=FailureType.TOOL_FAILURE,
                    source=FailureSource.RUNTIME,
                    message="当前策略仅允许 Workflow，但该任务没有可安全执行的工作流。",
                    retryable=False,
                    evidence=[
                        "workflow_activation_mode=workflow_only",
                        f"match_route={match.route.value}",
                    ],
                )
            ],
            workflow_execution=WorkflowExecutionRecord(
                task_id=task.task_id,
                # No Agent was called, but the task could not be executed by a registered
                # deterministic Workflow either. HYBRID is the existing neutral route for a
                # task requiring both platform workflows and possible Agent work; the failure
                # evidence below makes the policy rejection explicit to the UI.
                mode=WorkflowExecutionMode.HYBRID,
                workflow_id=match.workflow_id,
                fallback_reason=match.fallback_reason,
            ),
        )

    async def _run_agent(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        trace: TaskTraceCollector | None,
        continuation_context: ContextContinuationState | None,
    ) -> SingleTaskRunResult:
        agent_runner = self._agent_runner_factory()
        run_kwargs = {"workspace": workspace}
        if trace is not None:
            run_kwargs["trace"] = trace
        if continuation_context is not None:
            run_kwargs["continuation_context"] = continuation_context
        return await agent_runner.run(task, **run_kwargs)

    @staticmethod
    def _requires_agent_escalation(result: SingleTaskRunResult) -> bool:
        return any(
            failure.failure_type in {FailureType.TEST_FAILURE, FailureType.LINT_FAILURE}
            for failure in result.failures
        )
