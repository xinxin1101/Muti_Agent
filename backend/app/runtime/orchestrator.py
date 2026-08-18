from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.agents.errors import InvalidReviewerOutputError, RepairBudgetExhaustedError
from app.context import ContextBuildError, ContextPacketBuilder
from app.models import (
    AgentRole,
    DeveloperStopReason,
    FailureReport,
    FailureSource,
    FailureType,
    RepairStopReason,
    ReviewOutcome,
    TaskContract,
    TokenUsage,
)
from app.models.run import AgentUsageSummary, SingleTaskRunResult, TaskRunState
from app.providers.errors import AgentProviderError
from app.runtime.failure_classifier import FailureClassifier
from app.runtime.state_machine import TaskStateMachine
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace


class SingleTaskOrchestrator:
    """Execute the complete evidence-driven loop for one validated task."""

    def __init__(
        self,
        *,
        developer: DeveloperAgent,
        verifier: DeterministicVerifier,
        reviewer: ReviewerAgent,
        repair: RepairAgent,
        developer_model: str,
        reviewer_model: str,
        repair_model: str,
        context_builder: ContextPacketBuilder | None = None,
    ) -> None:
        self._developer = developer
        self._verifier = verifier
        self._reviewer = reviewer
        self._repair = repair
        self._context_builder = context_builder or ContextPacketBuilder()
        self._models = {
            AgentRole.DEVELOPER: self._normalize_model(developer_model, "developer"),
            AgentRole.REVIEWER: self._normalize_model(reviewer_model, "reviewer"),
            AgentRole.REPAIR: self._normalize_model(repair_model, "repair"),
        }

    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> SingleTaskRunResult:
        machine = TaskStateMachine()
        verifications = []
        reviews = []
        repairs = []
        developer_result = None

        machine.transition(TaskRunState.RUNNING, detail="Developer Agent started initial work.")
        try:
            developer_context = await self._build_context(task, workspace=workspace)
        except ContextBuildError as exc:
            machine.transition(
                TaskRunState.FAILED,
                detail="Developer ContextPacket construction failed.",
            )
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[self._context_failure(exc, stage="developer")],
            )

        try:
            developer_result = await self._developer.run(
                task,
                workspace=workspace,
                context_packet=developer_context,
            )
        except AgentProviderError as exc:
            machine.transition(TaskRunState.FAILED, detail="Developer model provider failed.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[exc.to_failure_report()],
            )
        except ValueError as exc:
            machine.transition(TaskRunState.FAILED, detail="Developer context gate failed.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[self._runtime_failure(str(exc))],
            )

        if developer_result.stop_reason is not DeveloperStopReason.MODEL_STOP:
            machine.transition(
                TaskRunState.FAILED,
                detail="Developer Agent stopped before a normal model completion.",
            )
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[
                    self._runtime_failure(
                        "Developer Agent did not reach a normal bounded completion.",
                        evidence=[f"stop_reason={developer_result.stop_reason.value}"],
                    )
                ],
            )

        if not workspace.changed_files():
            machine.transition(TaskRunState.FAILED, detail="Developer produced no Git changes.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[
                    self._runtime_failure(
                        "Developer Agent completed without producing repository changes.",
                        evidence=["changed_files=0"],
                    )
                ],
            )

        machine.transition(TaskRunState.VERIFYING, detail="Deterministic hard gate started.")

        while True:
            verification = await asyncio.to_thread(
                self._verifier.verify,
                task,
                workspace=workspace,
            )
            verifications.append(verification)

            if not verification.passed:
                failures = FailureClassifier.from_verification(verification)
                repairable = FailureClassifier.repairable(failures)
                if not repairable:
                    machine.transition(
                        TaskRunState.FAILED,
                        detail="Hard gate produced a non-repairable failure.",
                    )
                    return self._result(
                        task=task,
                        machine=machine,
                        workspace=workspace,
                        developer=developer_result,
                        verifications=verifications,
                        reviews=reviews,
                        repairs=repairs,
                        failures=failures or [self._empty_failure_evidence()],
                    )

                repair_result = await self._repair_once(
                    task=task,
                    failures=repairable,
                    workspace=workspace,
                    machine=machine,
                    attempt=len(repairs) + 1,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                )
                if isinstance(repair_result, SingleTaskRunResult):
                    return repair_result
                repairs.append(repair_result)
                machine.transition(
                    TaskRunState.VERIFYING,
                    detail=f"Repair attempt {repair_result.attempt} completed; hard gate reruns.",
                )
                continue

            machine.transition(
                TaskRunState.REVIEWING,
                detail="Hard gate passed; independent semantic review started.",
            )
            try:
                reviewer_context = await self._build_context(task, workspace=workspace)
            except ContextBuildError as exc:
                machine.transition(
                    TaskRunState.FAILED,
                    detail="Reviewer ContextPacket construction failed.",
                )
                return self._result(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=[self._context_failure(exc, stage="reviewer")],
                )

            try:
                decision = await self._reviewer.review(
                    task,
                    verification,
                    workspace=workspace,
                    context_packet=reviewer_context,
                )
            except InvalidReviewerOutputError as exc:
                machine.transition(
                    TaskRunState.FAILED,
                    detail="Reviewer output failed its structured-output gate.",
                )
                return self._result(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=[exc.failure],
                )
            except AgentProviderError as exc:
                machine.transition(TaskRunState.FAILED, detail="Reviewer model provider failed.")
                return self._result(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=[exc.to_failure_report()],
                )
            except ValueError as exc:
                machine.transition(TaskRunState.FAILED, detail="Reviewer evidence gate failed.")
                return self._result(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=[self._runtime_failure(str(exc))],
                )

            reviews.append(decision)
            if decision.decision is ReviewOutcome.PASS:
                machine.transition(
                    TaskRunState.SUCCEEDED,
                    detail="Hard evidence and independent semantic review both passed.",
                )
                return self._result(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=[],
                )

            failures = FailureClassifier.from_review(decision)
            repair_result = await self._repair_once(
                task=task,
                failures=failures,
                workspace=workspace,
                machine=machine,
                attempt=len(repairs) + 1,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
            )
            if isinstance(repair_result, SingleTaskRunResult):
                return repair_result
            repairs.append(repair_result)
            machine.transition(
                TaskRunState.VERIFYING,
                detail=f"Repair attempt {repair_result.attempt} completed; hard gate reruns.",
            )

    async def _repair_once(
        self,
        *,
        task: TaskContract,
        failures: Sequence[FailureReport],
        workspace: LocalGitWorkspace,
        machine: TaskStateMachine,
        attempt: int,
        developer,
        verifications,
        reviews,
        repairs,
    ):
        if attempt > task.max_retries:
            terminal = FailureClassifier.terminalize(
                failures,
                max_retries=task.max_retries,
            )
            machine.transition(TaskRunState.FAILED, detail="Repair retry budget was exhausted.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=terminal,
            )

        machine.transition(
            TaskRunState.REPAIRING,
            detail=f"Targeted repair attempt {attempt} started.",
        )
        try:
            repair_context = await self._build_context(task, workspace=workspace)
        except ContextBuildError as exc:
            machine.transition(
                TaskRunState.FAILED,
                detail="Repair ContextPacket construction failed.",
            )
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[self._context_failure(exc, stage="repair")],
            )

        try:
            repair_result = await self._repair.repair(
                task,
                failures,
                attempt=attempt,
                workspace=workspace,
                context_packet=repair_context,
            )
        except RepairBudgetExhaustedError as exc:
            machine.transition(
                TaskRunState.FAILED,
                detail="Repair Agent rejected exhausted budget.",
            )
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=exc.failures,
            )
        except AgentProviderError as exc:
            machine.transition(TaskRunState.FAILED, detail="Repair model provider failed.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[exc.to_failure_report()],
            )
        except ValueError as exc:
            machine.transition(TaskRunState.FAILED, detail="Repair evidence gate failed.")
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                failures=[self._runtime_failure(str(exc))],
            )

        if repair_result.stop_reason is not RepairStopReason.MODEL_STOP:
            machine.transition(
                TaskRunState.FAILED,
                detail="Repair Agent stopped before a normal model completion.",
            )
            targeted = ",".join(failure.failure_type.value for failure in failures)
            return self._result(
                task=task,
                machine=machine,
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                reviews=reviews,
                repairs=[*repairs, repair_result],
                failures=[
                    self._runtime_failure(
                        "Repair Agent did not reach a normal bounded completion.",
                        evidence=[
                            f"attempt={attempt}",
                            f"stop_reason={repair_result.stop_reason.value}",
                            f"target_failures={targeted}",
                        ],
                    )
                ],
            )

        return repair_result

    async def _build_context(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ):
        try:
            return await asyncio.to_thread(
                self._context_builder.build,
                task,
                workspace=workspace,
            )
        except ContextBuildError:
            raise
        except (OSError, ValueError) as exc:
            raise ContextBuildError(f"ContextPacket validation/read failed: {exc}") from exc

    def _result(
        self,
        *,
        task: TaskContract,
        machine: TaskStateMachine,
        workspace: LocalGitWorkspace,
        developer,
        verifications,
        reviews,
        repairs,
        failures: Sequence[FailureReport],
    ) -> SingleTaskRunResult:
        usage = []
        if developer is not None:
            usage.append(
                AgentUsageSummary(
                    role=AgentRole.DEVELOPER,
                    model=self._models[AgentRole.DEVELOPER],
                    calls=developer.iterations,
                    usage=developer.usage,
                    latency_ms=developer.latency_ms,
                )
            )
        if repairs:
            usage.append(
                AgentUsageSummary(
                    role=AgentRole.REPAIR,
                    model=self._models[AgentRole.REPAIR],
                    calls=sum(repair.iterations for repair in repairs),
                    usage=self._sum_usage(repair.usage for repair in repairs),
                    latency_ms=sum(repair.latency_ms for repair in repairs),
                )
            )

        return SingleTaskRunResult(
            task_id=task.task_id,
            status=machine.state,
            events=machine.events,
            developer=developer,
            verifications=list(verifications),
            reviews=list(reviews),
            repairs=list(repairs),
            failures=list(failures),
            changed_files=workspace.changed_files(),
            repair_attempts=len(repairs),
            agent_models=dict(self._models),
            agent_usage=usage,
        )

    @staticmethod
    def _sum_usage(usages) -> TokenUsage:
        prompt = 0
        completion = 0
        total = 0
        for usage in usages:
            prompt += usage.prompt_tokens
            completion += usage.completion_tokens
            total += usage.total_tokens
        return TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
        )

    @staticmethod
    def _context_failure(exc: ContextBuildError, *, stage: str) -> FailureReport:
        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message=f"{stage.capitalize()} ContextPacket construction failed.",
            retryable=False,
            evidence=[f"context_error={exc}"],
        )

    @staticmethod
    def _runtime_failure(message: str, *, evidence: list[str] | None = None) -> FailureReport:
        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message=message,
            retryable=False,
            evidence=evidence or [],
        )

    @staticmethod
    def _empty_failure_evidence() -> FailureReport:
        return SingleTaskOrchestrator._runtime_failure(
            "Verification failed without structured failure evidence."
        )

    @staticmethod
    def _normalize_model(value: str, role: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{role} model must not be empty")
        return normalized
