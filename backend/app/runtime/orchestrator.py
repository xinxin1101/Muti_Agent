from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from hashlib import sha256

from app.agents import DeveloperAgent, RepairAgent, ReviewerAgent
from app.agents.errors import InvalidReviewerOutputError, RepairBudgetExhaustedError
from app.context import ContextBuildError, ContextPacketBuilder
from app.models import (
    AgentRole,
    DeveloperStopReason,
    FailureReport,
    FailureSource,
    FailureType,
    RepairProgressEvidence,
    RepairProgressStatus,
    RepairStopReason,
    ReviewOutcome,
    TaskContract,
    TokenUsage,
)
from app.models.context import ContextContinuationState, ContextFileDigest, ContextPacket
from app.models.run import AgentUsageSummary, SingleTaskRunResult, TaskRunState
from app.providers.errors import AgentProviderError
from app.runtime.failure_classifier import FailureClassifier
from app.runtime.state_machine import TaskStateMachine
from app.trace.collector import TaskTraceCollector
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
        minimum_repair_attempts: int = 0,
    ) -> None:
        self._developer = developer
        self._verifier = verifier
        self._reviewer = reviewer
        self._repair = repair
        self._context_builder = context_builder or ContextPacketBuilder()
        if not 0 <= minimum_repair_attempts <= 5:
            raise ValueError("minimum_repair_attempts must be between 0 and 5")
        self._minimum_repair_attempts = minimum_repair_attempts
        self._models = {
            AgentRole.DEVELOPER: self._normalize_model(developer_model, "developer"),
            AgentRole.REVIEWER: self._normalize_model(reviewer_model, "reviewer"),
            AgentRole.REPAIR: self._normalize_model(repair_model, "repair"),
        }
        self._latest_context_packet: ContextPacket | None = None

    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        trace: TaskTraceCollector | None = None,
        continuation_context: ContextContinuationState | None = None,
    ) -> SingleTaskRunResult:
        self._latest_context_packet = None
        machine = TaskStateMachine()
        verifications = []
        reviews = []
        repairs = []
        developer_result = None

        machine.transition(TaskRunState.RUNNING, detail="Developer Agent started initial work.")
        try:
            developer_context = await self._build_context(
                task,
                workspace=workspace,
                resume=continuation_context,
            )
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
            if trace is None:
                developer_result = await self._developer.run(
                    task,
                    workspace=workspace,
                    context_packet=developer_context,
                )
            else:
                developer_result = await self._developer.run(
                    task,
                    workspace=workspace,
                    context_packet=developer_context,
                    trace=trace,
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
                failures=[self._developer_stop_failure(developer_result)],
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
            verification_started = trace.clock() if trace is not None else 0.0
            verification = await asyncio.to_thread(
                self._verifier.verify,
                task,
                workspace=workspace,
            )
            if trace is not None:
                trace.record_verification(
                    attempt=len(verifications) + 1,
                    result=verification,
                    duration_ms=trace.duration_ms(verification_started),
                )
            verifications.append(verification)

            if not verification.passed:
                failures = FailureClassifier.from_verification(verification)
                failure_signature = self._failure_signature(failures)
                self._record_repair_verification(
                    repairs,
                    verification=verification,
                    failure_signature=failure_signature,
                )
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
                    failures=self._repair_evidence_for_next_attempt(repairable, repairs),
                    failure_signature_before=failure_signature,
                    workspace=workspace,
                    machine=machine,
                    attempt=len(repairs) + 1,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    trace=trace,
                )
                if isinstance(repair_result, SingleTaskRunResult):
                    return repair_result
                repairs.append(repair_result)
                if repair_result.progress is not None and not repair_result.progress.has_patch:
                    return self._finish_no_progress(
                        task=task,
                        machine=machine,
                        workspace=workspace,
                        developer=developer_result,
                        verifications=verifications,
                        reviews=reviews,
                        repairs=repairs,
                        failures=failures,
                        repair_result=repair_result,
                    )
                machine.transition(
                    TaskRunState.VERIFYING,
                    detail=self._repair_verification_detail(repair_result),
                )
                continue

            self._record_repair_verification(
                repairs,
                verification=verification,
                failure_signature=None,
            )
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
                if trace is None:
                    decision = await self._reviewer.review(
                        task,
                        verification,
                        workspace=workspace,
                        context_packet=reviewer_context,
                    )
                else:
                    decision = await self._reviewer.review(
                        task,
                        verification,
                        workspace=workspace,
                        context_packet=reviewer_context,
                        trace=trace,
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
                failures=self._repair_evidence_for_next_attempt(failures, repairs),
                failure_signature_before=self._failure_signature(failures),
                workspace=workspace,
                machine=machine,
                attempt=len(repairs) + 1,
                developer=developer_result,
                verifications=verifications,
                reviews=reviews,
                repairs=repairs,
                trace=trace,
            )
            if isinstance(repair_result, SingleTaskRunResult):
                return repair_result
            repairs.append(repair_result)
            if repair_result.progress is not None and not repair_result.progress.has_patch:
                return self._finish_no_progress(
                    task=task,
                    machine=machine,
                    workspace=workspace,
                    developer=developer_result,
                    verifications=verifications,
                    reviews=reviews,
                    repairs=repairs,
                    failures=failures,
                    repair_result=repair_result,
                )
            machine.transition(
                TaskRunState.VERIFYING,
                detail=self._repair_verification_detail(repair_result),
            )

    async def _repair_once(
        self,
        *,
        task: TaskContract,
        failures: Sequence[FailureReport],
        failure_signature_before: str,
        workspace: LocalGitWorkspace,
        machine: TaskStateMachine,
        attempt: int,
        developer,
        verifications,
        reviews,
        repairs,
        trace: TaskTraceCollector | None,
    ):
        repair_budget = max(task.max_retries, self._minimum_repair_attempts)
        repair_task = task.model_copy(update={"max_retries": repair_budget})
        if attempt > repair_budget:
            terminal = FailureClassifier.terminalize(
                failures,
                max_retries=repair_budget,
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
            repair_context = await self._build_context(repair_task, workspace=workspace)
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
            before_state = workspace.change_snapshot()
            if trace is None:
                repair_result = await self._repair.repair(
                    repair_task,
                    failures,
                    attempt=attempt,
                    workspace=workspace,
                    context_packet=repair_context,
                )
            else:
                repair_result = await self._repair.repair(
                    repair_task,
                    failures,
                    attempt=attempt,
                    workspace=workspace,
                    context_packet=repair_context,
                    trace=trace,
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

        after_state = workspace.change_snapshot()
        has_patch = before_state.patch_hash != after_state.patch_hash
        progress = RepairProgressEvidence(
            status=(
                RepairProgressStatus.PATCH_PRODUCED
                if has_patch
                else RepairProgressStatus.NO_PATCH_PRODUCED
            ),
            has_patch=has_patch,
            files_changed=after_state.files_changed_since(before_state),
            patch_hash_before=before_state.patch_hash,
            patch_hash_after=after_state.patch_hash,
            failure_signature_before=failure_signature_before,
        )
        return repair_result.model_copy(
            update={
                "changed_files": list(after_state.changed_files),
                "progress": progress,
            }
        )

    async def _build_context(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        resume: ContextContinuationState | None = None,
    ):
        try:
            kwargs = {"workspace": workspace}
            if resume is not None:
                kwargs["resume"] = resume
            packet = await asyncio.to_thread(self._context_builder.build, task, **kwargs)
            self._latest_context_packet = packet
            return packet
        except ContextBuildError:
            raise
        except (OSError, ValueError) as exc:
            raise ContextBuildError(f"ContextPacket validation/read failed: {exc}") from exc

    @staticmethod
    def _repair_verification_detail(repair_result) -> str:
        if repair_result.stop_reason is RepairStopReason.TIME_LIMIT:
            return (
                f"Repair attempt {repair_result.attempt} reached its time limit; "
                "hard gate reruns before a bounded continuation."
            )
        if repair_result.stop_reason is RepairStopReason.ITERATION_LIMIT:
            return (
                f"Repair attempt {repair_result.attempt} reached its iteration limit; "
                "hard gate reruns before deciding whether another repair is needed."
            )
        if repair_result.stop_reason is RepairStopReason.TOOL_CALL_LIMIT:
            return (
                f"Repair attempt {repair_result.attempt} reached its tool-call limit; "
                "hard gate reruns before deciding whether another repair is needed."
            )
        return f"Repair attempt {repair_result.attempt} completed; hard gate reruns."

    @staticmethod
    def _failure_signature(failures: Sequence[FailureReport]) -> str:
        """Hash stable failure content without volatile traceback addresses or whitespace."""

        normalized = []
        for failure in failures:
            evidence = "\n".join(
                SingleTaskOrchestrator._normalize_failure_text(item) for item in failure.evidence
            )
            normalized.append(
                "\n".join(
                    (
                        failure.failure_type.value,
                        failure.source.value,
                        SingleTaskOrchestrator._normalize_failure_text(failure.message),
                        evidence,
                    )
                )
            )
        return sha256("\n\n".join(sorted(normalized)).encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_failure_text(value: str) -> str:
        without_addresses = re.sub(r"0x[0-9a-fA-F]+", "0x<address>", value)
        return " ".join(without_addresses.split())

    @staticmethod
    def _validation_commands(verification) -> list[str]:
        return [check.command for check in verification.checks if check.command]

    def _record_repair_verification(
        self,
        repairs,
        *,
        verification,
        failure_signature: str | None,
    ) -> None:
        if not repairs:
            return
        repair = repairs[-1]
        progress = repair.progress
        if progress is None or progress.status is not RepairProgressStatus.PATCH_PRODUCED:
            return
        status = RepairProgressStatus.REPAIRED
        if not verification.passed:
            status = (
                RepairProgressStatus.REPAIR_INEFFECTIVE
                if progress.failure_signature_before == failure_signature
                else RepairProgressStatus.PROGRESS_MADE
            )
        repairs[-1] = repair.model_copy(
            update={
                "progress": progress.model_copy(
                    update={
                        "status": status,
                        "failure_signature_after": failure_signature,
                        "validation_executed": True,
                        "validation_commands": self._validation_commands(verification),
                    }
                )
            }
        )

    @staticmethod
    def _repair_evidence_for_next_attempt(failures, repairs):
        if not repairs or repairs[-1].progress is None:
            return failures
        progress = repairs[-1].progress
        evidence = [
            f"previous_repair_progress={progress.status.value}",
            f"previous_patch_hash={progress.patch_hash_after}",
            f"previous_failure_signature_before={progress.failure_signature_before}",
            f"previous_failure_signature_after={progress.failure_signature_after}",
        ]
        if progress.files_changed:
            evidence.append("previous_files_changed=" + ",".join(progress.files_changed))
        return [
            failure.model_copy(update={"evidence": [*failure.evidence, *evidence]})
            for failure in failures
        ]

    def _finish_no_progress(
        self,
        *,
        task,
        machine,
        workspace,
        developer,
        verifications,
        reviews,
        repairs,
        failures,
        repair_result,
    ) -> SingleTaskRunResult:
        assert repair_result.progress is not None
        progress = repair_result.progress
        machine.transition(
            TaskRunState.FAILED,
            detail="Repair Agent produced no workspace patch; downstream verification was skipped.",
        )
        terminal_failures = [
            failure.model_copy(
                update={
                    "retryable": False,
                    "message": (
                        f"{failure.message} Repair Agent produced no candidate patch; "
                        "downstream verification was not rerun."
                    ),
                    "evidence": [
                        *failure.evidence,
                        f"repair_progress={progress.status.value}",
                        "patch_produced=false",
                        f"repair_stop_reason={repair_result.stop_reason.value}",
                        f"patch_hash_before={progress.patch_hash_before}",
                        f"patch_hash_after={progress.patch_hash_after}",
                        f"failure_signature_before={progress.failure_signature_before}",
                        "validation_executed=false",
                    ],
                }
            )
            for failure in failures
        ]
        return self._result(
            task=task,
            machine=machine,
            workspace=workspace,
            developer=developer,
            verifications=verifications,
            reviews=reviews,
            repairs=repairs,
            failures=terminal_failures,
        )

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
            context_state=self._current_context_state(
                workspace=workspace,
                developer=developer,
                verifications=verifications,
                failures=failures,
            ),
        )

    def _current_context_state(
        self,
        *,
        workspace: LocalGitWorkspace,
        developer,
        verifications,
        failures: Sequence[FailureReport],
    ) -> ContextContinuationState | None:
        packet = self._latest_context_packet
        if packet is None:
            return None
        verification_summary = ""
        if verifications:
            latest_result = "通过" if verifications[-1].passed else "失败"
            verification_summary = f"已执行 {len(verifications)} 次验证；最近结果={latest_result}。"
        return ContextContinuationState(
            summary_version=packet.repository_summary_version,
            repository_head=packet.repository_head,
            read_files=tuple(
                ContextFileDigest(path=item.path, source_sha256=item.source_sha256)
                for item in packet.selected_files
            ),
            changed_files=tuple(workspace.changed_files()),
            completed_summary=(
                developer.final_message[:512]
                if developer is not None and developer.final_message
                else "已完成当前切片的受控代码修改。"
            ),
            remaining_summary=(
                failures[0].message[:512] if failures else "继续完成尚未满足的验收条件。"
            ),
            verification_summary=verification_summary,
            failure_summary=failures[0].message[:512] if failures else "",
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
    def _developer_stop_failure(developer_result) -> FailureReport:
        """Classify controlled Developer exits without misreporting them as tool faults."""

        evidence = [f"stop_reason={developer_result.stop_reason.value}"]
        budget = developer_result.execution_budget
        if budget is not None:
            evidence.extend(
                [
                    f"developer_max_iterations={budget.max_iterations}",
                    f"developer_max_duration_seconds={budget.max_duration_seconds:g}",
                    f"developer_max_model_turn_seconds={budget.max_model_turn_seconds:g}",
                ]
            )
        evidence.append(f"developer_model_latency_ms={developer_result.latency_ms}")

        if developer_result.stop_reason is DeveloperStopReason.TIME_LIMIT:
            return FailureReport(
                failure_type=FailureType.AGENT_TIME_LIMIT,
                source=FailureSource.RUNTIME,
                message="开发智能体时间预算耗尽，未能在限制内完成代码修改。",
                retryable=False,
                evidence=evidence,
            )

        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message="Developer Agent did not reach a normal bounded completion.",
            retryable=False,
            evidence=evidence,
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
