from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models.checkpoint import CheckpointReason, TaskCheckpoint
from app.models.context import ContextContinuationState
from app.models.continuation import TaskContinuationSummary
from app.models.developer import DeveloperStopReason
from app.models.dispatch import (
    TaskDispatchEnvelope,
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.lease import TaskLeaseSnapshot
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.run_reconciliation import TaskExecutionBase
from app.models.task import TaskContract
from app.persistence import PersistenceEvidenceKind
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus
from app.planning import assess_task_complexity
from app.workspace import LocalGitWorkspace, TaskWorktreeManager, TaskWorktreeRecord


class SingleTaskRunner(Protocol):
    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
        continuation_context: ContextContinuationState | None = None,
        resume_verification_first: bool = False,
    ) -> SingleTaskRunResult: ...


class ProjectWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class TaskGitMutationFence(Protocol):
    def guard_task_git_mutation(
        self,
        *,
        run_id: UUID,
        task_id: str,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> AbstractAsyncContextManager[TaskLeaseSnapshot]: ...


class QueuedTaskExecutionBackend(Protocol):
    async def execute(
        self,
        *,
        task: TaskContract,
        project_id: UUID,
        run_id: UUID,
        dispatch_id: UUID,
        run_token: UUID,
        base_commit: str,
        continuation_context: ContextContinuationState | None = None,
        resume_verification_first: bool = False,
    ) -> WorkerExecutionEvidence: ...


class WorkerEvidenceStore(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...

    async def append_evidence(
        self,
        *,
        run_id: UUID,
        evidence_key: str,
        kind: PersistenceEvidenceKind,
        payload_model: BaseModel,
        task_id: str | None = None,
        stage: str | None = None,
        sequence: int | None = None,
        run_token: UUID | None = None,
    ) -> int: ...

    async def finalize_single_task_run(
        self,
        *,
        run_id: UUID,
        result: SingleTaskRunResult,
        run_token: UUID | None = None,
    ) -> None: ...


class QueuedTaskExecutionBaseResolver(Protocol):
    async def resolve(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> TaskExecutionBase: ...


class QueuedTaskDAGReader(Protocol):
    async def load_dag(self, run_id: UUID): ...


class QueuedTaskBudgetReader(Protocol):
    async def snapshot(self, run_id: UUID): ...


class QueuedTaskBudgetManager(QueuedTaskBudgetReader, Protocol):
    async def reclaim_unused_task_budget(self, *, run_id: UUID, task_id: str) -> None: ...


class WorkerInterfaceContractRegistry(Protocol):
    async def mark_producer_satisfied(
        self, *, run_id: UUID, task_id: str, commit_sha: str | None
    ) -> None: ...

    async def mark_producer_unmet(self, *, run_id: UUID, task_id: str) -> None: ...


class ManagedProjectWorkspaceResolver:
    """Resolve a project id to an already-materialized managed Git repository."""

    def __init__(self, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        if candidate.exists() and candidate.is_symlink():
            raise ValueError("managed repository root must not be a symbolic link")
        self._root = candidate.resolve(strict=False)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, project_id: UUID) -> LocalGitWorkspace:
        return LocalGitWorkspace(self._root / str(project_id))


class LocalQueuedTaskExecutionBackend:
    """Execute one fenced persisted generation through worktree + single-task runtime."""

    def __init__(
        self,
        *,
        workspace_resolver: ProjectWorkspaceResolver,
        worktree_root: str | Path,
        runner_factory: Callable[[TaskContract], SingleTaskRunner],
        runner_factory_for_execution: (
            Callable[[TaskContract, UUID], SingleTaskRunner] | None
        ) = None,
        git_fence: TaskGitMutationFence,
    ) -> None:
        root = Path(worktree_root).expanduser()
        if root.exists() and root.is_symlink():
            raise ValueError("queued-task worktree root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        self._workspace_resolver = workspace_resolver
        self._worktree_root = root.resolve()
        self._runner_factory = runner_factory
        self._runner_factory_for_execution = runner_factory_for_execution
        self._git_fence = git_fence

    async def execute(
        self,
        *,
        task: TaskContract,
        project_id: UUID,
        run_id: UUID,
        dispatch_id: UUID,
        run_token: UUID,
        base_commit: str,
        continuation_context: ContextContinuationState | None = None,
        resume_verification_first: bool = False,
    ) -> WorkerExecutionEvidence:
        started_at = perf_counter()
        record: TaskWorktreeRecord | None = None
        run_result: SingleTaskRunResult | None = None
        worktree_identity = self._worktree_identity(
            run_id,
            task.task_id,
            run_token,
            base_commit,
        )
        try:
            base_workspace = self._workspace_resolver.resolve(project_id)
            worktrees = TaskWorktreeManager(
                base_workspace,
                self._worktree_root / str(run_id),
                frozen_base_commit=base_commit,
            )

            # `git worktree add -b` creates a branch ref, so workspace creation is itself a
            # runtime-owned Git mutation and must be fenced against stale generations.
            async with self._git_fence.guard_task_git_mutation(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                run_token=run_token,
            ):
                record = await asyncio.to_thread(worktrees.create, worktree_identity)
            workspace = worktrees.open_workspace(worktree_identity)

            runner = (
                self._runner_factory_for_execution(task, run_id)
                if self._runner_factory_for_execution is not None
                else self._runner_factory(task)
            )
            run_kwargs = {"workspace": workspace}
            if continuation_context is not None:
                run_kwargs["continuation_context"] = continuation_context
            if resume_verification_first:
                run_kwargs["resume_verification_first"] = True
            run_result = await runner.run(task, **run_kwargs)
            if run_result.status is TaskRunState.FAILED:
                checkpoint = await self._checkpoint_failed_work(
                    worktrees=worktrees,
                    worktree_identity=worktree_identity,
                    task=task,
                    base_commit=base_commit,
                    workspace=workspace,
                    run_result=run_result,
                    run_id=run_id,
                    dispatch_id=dispatch_id,
                    run_token=run_token,
                )
                return self._failure_evidence(
                    task=task,
                    run_id=run_id,
                    dispatch_id=dispatch_id,
                    base_commit=base_commit,
                    started_at=started_at,
                    record=record,
                    run_result=run_result,
                    failures=tuple(run_result.failures),
                    checkpoint=checkpoint,
                )
            if run_result.status is not TaskRunState.SUCCEEDED:
                raise RuntimeError("single-task runtime returned a non-terminal status")

            # The database task-row lock is held while Git publishes the generation result.
            # Takeover therefore cannot replace the token between validation and update-ref.
            async with self._git_fence.guard_task_git_mutation(
                run_id=run_id,
                task_id=task.task_id,
                dispatch_id=dispatch_id,
                run_token=run_token,
            ):
                commit_sha = await asyncio.to_thread(
                    worktrees.commit_task_changes,
                    worktree_identity,
                )
            return WorkerExecutionEvidence(
                dispatch_id=dispatch_id,
                run_id=run_id,
                task_id=task.task_id,
                status=WorkerExecutionStatus.SUCCEEDED,
                base_commit=base_commit,
                branch_name=record.branch_name,
                commit_sha=commit_sha,
                run_result=run_result,
                failures=(),
                duration_ms=self._duration_ms(started_at),
            )
        except Exception as exc:
            failure = self._runtime_failure(
                "Queued worker execution failed before fenced task-branch finalization.",
                evidence=[f"exception_type={type(exc).__name__}"],
            )
            return self._failure_evidence(
                task=task,
                run_id=run_id,
                dispatch_id=dispatch_id,
                base_commit=base_commit,
                started_at=started_at,
                record=record,
                run_result=run_result,
                failures=(failure,),
                checkpoint=None,
            )

    async def _checkpoint_failed_work(
        self,
        *,
        worktrees: TaskWorktreeManager,
        worktree_identity: str,
        task: TaskContract,
        base_commit: str,
        workspace: LocalGitWorkspace,
        run_result: SingleTaskRunResult,
        run_id: UUID,
        dispatch_id: UUID,
        run_token: UUID,
    ) -> TaskCheckpoint | None:
        """Preserve useful, fenced edits without treating a failed task as successful."""

        changed_files = tuple(workspace.changed_files())
        if not changed_files:
            return None
        reason = CheckpointReason.VERIFICATION_FAILURE
        if run_result.developer is not None:
            reason = {
                DeveloperStopReason.TIME_LIMIT: CheckpointReason.TIME_LIMIT,
                DeveloperStopReason.ITERATION_LIMIT: CheckpointReason.ITERATION_LIMIT,
                DeveloperStopReason.TOOL_CALL_LIMIT: CheckpointReason.TOOL_CALL_LIMIT,
            }.get(run_result.developer.stop_reason, reason)
        async with self._git_fence.guard_task_git_mutation(
            run_id=run_id,
            task_id=task.task_id,
            dispatch_id=dispatch_id,
            run_token=run_token,
        ):
            commit_sha = await asyncio.to_thread(
                worktrees.commit_task_changes,
                worktree_identity,
            )
        context_state = run_result.context_state
        if context_state is not None:
            # The checkpoint commit, not the pre-slice worktree head, is the durable
            # truth for the next compact Agent session.
            context_state = context_state.model_copy(update={"repository_head": commit_sha})
        return TaskCheckpoint(
            task_id=task.task_id,
            base_commit=base_commit,
            commit_sha=commit_sha,
            changed_files=changed_files,
            reason=reason,
            summary=(
                "已保存当前受控代码改动；从检查点继续时会复用仓库摘要和未变更文件哈希，"
                "仅发送改动文件哈希、验证摘要与未完成工作。"
            ),
            context_state=context_state,
        )

    @staticmethod
    def _worktree_identity(
        run_id: UUID,
        task_id: str,
        run_token: UUID,
        base_commit: str,
    ) -> str:
        generation_digest = hashlib.sha256(
            f"{task_id}:{run_token.hex}:{base_commit}".encode()
        ).hexdigest()[:16]
        return f"run-{run_id.hex}-generation-{generation_digest}"

    def _failure_evidence(
        self,
        *,
        task: TaskContract,
        run_id: UUID,
        dispatch_id: UUID,
        base_commit: str,
        started_at: float,
        record: TaskWorktreeRecord | None,
        run_result: SingleTaskRunResult | None,
        failures: tuple[FailureReport, ...],
        checkpoint: TaskCheckpoint | None,
    ) -> WorkerExecutionEvidence:
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.FAILED,
            base_commit=base_commit,
            branch_name=record.branch_name if record is not None else None,
            commit_sha=None,
            checkpoint=checkpoint,
            run_result=run_result,
            failures=failures,
            duration_ms=self._duration_ms(started_at),
        )

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))

    @staticmethod
    def _runtime_failure(message: str, *, evidence: list[str]) -> FailureReport:
        return FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message=message,
            retryable=False,
            evidence=evidence,
        )


class QueuedTaskWorker:
    """Reload persisted identity and persist only writes authorized by the current token."""

    def __init__(
        self,
        *,
        store: WorkerEvidenceStore,
        backend: QueuedTaskExecutionBackend,
        execution_base_resolver: QueuedTaskExecutionBaseResolver | None = None,
        continuation_max_slices: int = 5,
        continuation_total_budget_seconds: float = 3_600.0,
        continuation_max_repeated_file_slices: int = 1,
        interface_contract_registry: WorkerInterfaceContractRegistry | None = None,
        dag_reader: QueuedTaskDAGReader | None = None,
        token_budget_reader: QueuedTaskBudgetReader | None = None,
        token_budget_manager: QueuedTaskBudgetManager | None = None,
    ) -> None:
        if not 1 <= continuation_max_slices <= 20:
            raise ValueError("continuation_max_slices must be between 1 and 20")
        if not 600.0 <= continuation_total_budget_seconds <= 86_400.0:
            raise ValueError("continuation_total_budget_seconds must be between 600 and 86400")
        if not 0 <= continuation_max_repeated_file_slices <= 5:
            raise ValueError("continuation_max_repeated_file_slices must be between 0 and 5")
        self._store = store
        self._backend = backend
        self._execution_base_resolver = execution_base_resolver
        self._continuation_max_slices = continuation_max_slices
        self._continuation_total_budget_seconds = continuation_total_budget_seconds
        self._continuation_max_repeated_file_slices = continuation_max_repeated_file_slices
        self._interface_contract_registry = interface_contract_registry
        self._dag_reader = dag_reader
        self._token_budget_reader = token_budget_reader
        self._token_budget_manager = token_budget_manager

    async def execute(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
    ) -> WorkerExecutionEvidence:
        snapshot = await self._store.load_run(envelope.run_id)
        if snapshot.status is not PersistedRunStatus.RUNNING:
            raise WorkerExecutionBoundaryError("worker may execute only persisted RUNNING runs")

        task = self._task_from_snapshot(snapshot, envelope.task_id)
        node = None
        if self._dag_reader is not None:
            node = (await self._dag_reader.load_dag(envelope.run_id)).dag.node(envelope.task_id)
        complexity = assess_task_complexity(task)
        if node is not None and node.complexity is not None:
            # Planner-declared complexity is the durable scheduling contract; the heuristic is
            # retained only for legacy DAGs that predate WorkPackage metadata.
            complexity = replace(
                complexity,
                score=max(
                    complexity.score,
                    {"LOW": 1, "MEDIUM": 4, "HIGH": 8}[node.complexity.value],
                ),
            )
        effective_max_slices = self._effective_max_slices(complexity.score)
        terminal_execution: WorkerExecutionEvidence | None = None
        execution_evidence_persisted = False
        try:
            execution_base = await self._resolve_execution_base(snapshot, envelope.task_id)
            await self._record_dispatch_event(
                envelope,
                run_token=run_token,
                phase=WorkerDispatchPhase.RECEIVED,
            )

            continuation_started = perf_counter()
            slice_index = 1
            previous_changed_files: tuple[str, ...] | None = None
            repeated_file_slices = 0
            resumed_from_commits: list[str] = []
            continuation_context: ContextContinuationState | None = (
                node.resume_context.context_state
                if node is not None and node.resume_context is not None
                else None
            )
            resume_verification_first = bool(
                node is not None
                and node.resume_context is not None
                and node.resume_context.strategy.value == "VERIFY_THEN_REPAIR"
            )
            while True:
                execution_kwargs = {
                    "task": task,
                    "project_id": snapshot.project_id,
                    "run_id": snapshot.run_id,
                    "dispatch_id": envelope.dispatch_id,
                    "run_token": run_token,
                    "base_commit": execution_base,
                }
                if continuation_context is not None:
                    execution_kwargs["continuation_context"] = continuation_context
                if resume_verification_first:
                    execution_kwargs["resume_verification_first"] = True
                execution = await self._backend.execute(**execution_kwargs)
                checkpoint = self._checkpoint_with_slice_facts(
                    execution=execution,
                    slice_index=slice_index,
                    elapsed_ms=self._elapsed_ms(continuation_started),
                    resume_from_commit=(resumed_from_commits[-1] if resumed_from_commits else None),
                    max_slices=effective_max_slices,
                    remaining_interfaces=tuple(node.produces) if node is not None else (),
                )
                if checkpoint is not None:
                    execution = execution.model_copy(update={"checkpoint": checkpoint})
                    execution = await self._checkpoint_budget_facts(
                        execution=execution,
                        run_id=envelope.run_id,
                    )

                if not self._can_continue(
                    execution=execution,
                    slice_index=slice_index,
                    max_slices=effective_max_slices,
                    elapsed_ms=self._elapsed_ms(continuation_started),
                    previous_changed_files=previous_changed_files,
                    repeated_file_slices=repeated_file_slices,
                ):
                    break

                assert checkpoint is not None
                await self._persist_runtime_result(
                    envelope,
                    execution.run_result,
                    run_token=run_token,
                    slice_index=slice_index,
                )
                await self._store.append_evidence(
                    run_id=envelope.run_id,
                    task_id=envelope.task_id,
                    evidence_key=f"dispatch:{envelope.dispatch_id}:checkpoint:slice:{slice_index:04d}",
                    kind=PersistenceEvidenceKind.TASK_CHECKPOINT,
                    payload_model=checkpoint,
                    stage="checkpoint",
                    sequence=slice_index,
                    run_token=run_token,
                )
                changed_files = checkpoint.changed_files
                repeated_file_slices = (
                    repeated_file_slices + 1 if changed_files == previous_changed_files else 0
                )
                previous_changed_files = changed_files
                resumed_from_commits.append(checkpoint.commit_sha)
                execution_base = checkpoint.commit_sha
                continuation_context = checkpoint.context_state
                # Later slices were created by a bounded Developer stop, so normal
                # continuation is correct after the first resumed verification pass.
                resume_verification_first = False
                slice_index += 1

            if slice_index > 1:
                execution = execution.model_copy(
                    update={
                        "continuation": TaskContinuationSummary(
                            slices_started=slice_index,
                            max_slices=effective_max_slices,
                            total_budget_seconds=self._continuation_total_budget_seconds,
                            elapsed_ms=self._elapsed_ms(continuation_started),
                            complexity_score=complexity.score,
                            resumed_from_commits=tuple(resumed_from_commits),
                            stop_reason=(
                                execution.run_result.developer.stop_reason.value
                                if execution.run_result is not None
                                and execution.run_result.developer is not None
                                else execution.status.value
                            ),
                        )
                    }
                )

            await self._persist_runtime_result(
                envelope,
                execution.run_result,
                run_token=run_token,
                slice_index=slice_index if slice_index > 1 else None,
            )
            if execution.checkpoint is not None:
                await self._store.append_evidence(
                    run_id=envelope.run_id,
                    task_id=envelope.task_id,
                    evidence_key=(
                        f"dispatch:{envelope.dispatch_id}:checkpoint"
                        if slice_index == 1
                        else f"dispatch:{envelope.dispatch_id}:checkpoint:slice:{slice_index:04d}"
                    ),
                    kind=PersistenceEvidenceKind.TASK_CHECKPOINT,
                    payload_model=execution.checkpoint,
                    stage="checkpoint",
                    sequence=slice_index if slice_index > 1 else None,
                    run_token=run_token,
                )
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"dispatch:{envelope.dispatch_id}:execution",
                kind=PersistenceEvidenceKind.WORKER_EXECUTION,
                payload_model=execution,
                stage="worker",
                run_token=run_token,
            )
            terminal_execution = execution
            execution_evidence_persisted = True
            if self._interface_contract_registry is not None:
                if execution.status is WorkerExecutionStatus.SUCCEEDED:
                    await self._interface_contract_registry.mark_producer_satisfied(
                        run_id=envelope.run_id,
                        task_id=envelope.task_id,
                        commit_sha=execution.commit_sha,
                    )
                else:
                    await self._interface_contract_registry.mark_producer_unmet(
                        run_id=envelope.run_id,
                        task_id=envelope.task_id,
                    )
            if self._token_budget_manager is not None:
                # A failed terminal attempt cannot safely keep capacity borrowed from
                # dependency-blocked packages. Reclaim/settle it just as a successful
                # task does; the immutable Run evidence remains untouched.
                await self._token_budget_manager.reclaim_unused_task_budget(
                    run_id=envelope.run_id,
                    task_id=envelope.task_id,
                )
            await self._record_dispatch_event(
                envelope,
                run_token=run_token,
                phase=WorkerDispatchPhase.COMPLETED,
                outcome=execution.status,
            )

            if (
                len(snapshot.tasks) == 1
                and execution.run_result is not None
                and (
                    (
                        execution.status is WorkerExecutionStatus.SUCCEEDED
                        and execution.run_result.status is TaskRunState.SUCCEEDED
                    )
                    or (
                        execution.status is WorkerExecutionStatus.FAILED
                        and execution.run_result.status is TaskRunState.FAILED
                    )
                )
            ):
                await self._store.finalize_single_task_run(
                    run_id=envelope.run_id,
                    result=execution.run_result,
                    run_token=run_token,
                )
            return execution
        except WorkerExecutionBoundaryError as exc:
            if execution_evidence_persisted:
                assert terminal_execution is not None
                await self._persist_post_execution_finalization_failure(
                    envelope=envelope,
                    run_token=run_token,
                    exception=exc,
                    execution=terminal_execution,
                )
                raise
            # A boundary violation must remain visible to the lease/actor layers so they can
            # fail closed, but it also needs durable evidence before the lease is released.
            await self._persist_terminal_worker_exception(
                envelope=envelope,
                snapshot=snapshot,
                run_token=run_token,
                exception=exc,
                boundary_violation=True,
            )
            raise
        except Exception as exc:
            if execution_evidence_persisted:
                assert terminal_execution is not None
                await self._persist_post_execution_finalization_failure(
                    envelope=envelope,
                    run_token=run_token,
                    exception=exc,
                    execution=terminal_execution,
                )
                return terminal_execution
            # Other runtime failures are converted to terminal evidence so a released lease
            # can never masquerade as a live but invisible task.
            return await self._persist_terminal_worker_exception(
                envelope=envelope,
                snapshot=snapshot,
                run_token=run_token,
                exception=exc,
                boundary_violation=False,
            )

    async def _persist_post_execution_finalization_failure(
        self,
        *,
        envelope: TaskDispatchEnvelope,
        run_token: UUID,
        exception: Exception,
        execution: WorkerExecutionEvidence,
    ) -> None:
        """Record cleanup failures without replacing already immutable execution evidence."""

        failure = FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message="Worker 已保存执行证据，但在完成预算或分派收尾时发生异常。",
            retryable=False,
            evidence=(
                "terminalization=post_execution_finalization_failure",
                f"exception_type={type(exception).__name__}",
                "execution_evidence_preserved=true",
            ),
        )
        await self._store.append_evidence(
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            evidence_key=f"dispatch:{envelope.dispatch_id}:post-execution-finalization-failure",
            kind=PersistenceEvidenceKind.FAILURE_REPORT,
            payload_model=failure,
            stage="runtime",
            run_token=run_token,
        )
        await self._record_dispatch_event(
            envelope,
            run_token=run_token,
            phase=WorkerDispatchPhase.COMPLETED,
            outcome=execution.status,
        )

    async def _persist_terminal_worker_exception(
        self,
        *,
        envelope: TaskDispatchEnvelope,
        snapshot: PersistedRunSnapshot,
        run_token: UUID,
        exception: Exception,
        boundary_violation: bool,
    ) -> WorkerExecutionEvidence:
        evidence = [
            "terminalization=worker_exception",
            f"exception_type={type(exception).__name__}",
        ]
        if boundary_violation:
            evidence.append("failure_class=execution_boundary")
        failure = FailureReport(
            failure_type=FailureType.TOOL_FAILURE,
            source=FailureSource.RUNTIME,
            message="Worker 在持久化终态前遇到未处理异常。",
            retryable=False,
            evidence=evidence,
        )
        execution = WorkerExecutionEvidence(
            dispatch_id=envelope.dispatch_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            status=WorkerExecutionStatus.FAILED,
            base_commit=snapshot.base_commit,
            failures=(failure,),
            duration_ms=0,
        )
        await self._store.append_evidence(
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            evidence_key=f"dispatch:{envelope.dispatch_id}:execution",
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            payload_model=execution,
            stage="worker",
            run_token=run_token,
        )
        await self._record_dispatch_event(
            envelope,
            run_token=run_token,
            phase=WorkerDispatchPhase.COMPLETED,
            outcome=WorkerExecutionStatus.FAILED,
        )
        return execution

    async def _resolve_execution_base(
        self,
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> str:
        if self._execution_base_resolver is not None:
            resolved = await self._execution_base_resolver.resolve(
                snapshot=snapshot,
                task_id=task_id,
            )
            if resolved.run_id != snapshot.run_id or resolved.task_id != task_id:
                raise WorkerExecutionBoundaryError(
                    "queued execution-base resolver returned mismatched Run/Task identity"
                )
            return resolved.commit_sha
        if len(snapshot.tasks) != 1:
            raise WorkerExecutionBoundaryError(
                "multi-task queued execution requires an evidence-bound DAG execution-base resolver"
            )
        return snapshot.base_commit

    @staticmethod
    def _task_from_snapshot(snapshot: PersistedRunSnapshot, task_id: str) -> TaskContract:
        matches = [item.task for item in snapshot.tasks if item.task.task_id == task_id]
        if len(matches) != 1:
            raise WorkerExecutionBoundaryError(
                f"persisted task {task_id!r} is not uniquely bound to run {snapshot.run_id}"
            )
        return matches[0]

    async def _record_dispatch_event(
        self,
        envelope: TaskDispatchEnvelope,
        *,
        run_token: UUID,
        phase: WorkerDispatchPhase,
        outcome: WorkerExecutionStatus | None = None,
    ) -> None:
        event = WorkerDispatchEvent(
            dispatch_id=envelope.dispatch_id,
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            phase=phase,
            outcome=outcome,
        )
        await self._store.append_evidence(
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            evidence_key=f"dispatch:{envelope.dispatch_id}:{phase.value.lower()}",
            kind=PersistenceEvidenceKind.DISPATCH_EVENT,
            payload_model=event,
            stage="worker",
            run_token=run_token,
        )

    async def _persist_runtime_result(
        self,
        envelope: TaskDispatchEnvelope,
        result: SingleTaskRunResult | None,
        *,
        run_token: UUID,
        slice_index: int | None = None,
    ) -> None:
        if result is None:
            return

        prefix = f"dispatch:{envelope.dispatch_id}"
        if slice_index is not None:
            prefix = f"{prefix}:slice:{slice_index:04d}"
        for event in result.events:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:state:{event.sequence:04d}",
                kind=PersistenceEvidenceKind.STATE_TRANSITION,
                payload_model=event,
                stage="runtime",
                sequence=event.sequence,
                run_token=run_token,
            )

        if result.developer is not None:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:developer",
                kind=PersistenceEvidenceKind.DEVELOPER_RUN,
                payload_model=result.developer,
                stage="developer",
                run_token=run_token,
            )

        for index, verification in enumerate(result.verifications):
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:verification:{index:04d}",
                kind=PersistenceEvidenceKind.VERIFICATION_RESULT,
                payload_model=verification,
                stage="verification",
                sequence=index,
                run_token=run_token,
            )

        for index, review in enumerate(result.reviews):
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:review:{index:04d}",
                kind=PersistenceEvidenceKind.REVIEW_DECISION,
                payload_model=review,
                stage="review",
                sequence=index,
                run_token=run_token,
            )

        for repair in result.repairs:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:repair:{repair.attempt:04d}",
                kind=PersistenceEvidenceKind.REPAIR_RUN,
                payload_model=repair,
                stage="repair",
                sequence=repair.attempt,
                run_token=run_token,
            )

        for index, failure in enumerate(result.failures):
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:failure:{index:04d}",
                kind=PersistenceEvidenceKind.FAILURE_REPORT,
                payload_model=failure,
                stage="runtime",
                sequence=index,
                run_token=run_token,
            )

        if result.workflow_execution is not None:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:workflow",
                kind=PersistenceEvidenceKind.WORKFLOW_EXECUTION,
                payload_model=result.workflow_execution,
                stage="workflow",
                run_token=run_token,
            )

    def _can_continue(
        self,
        *,
        execution: WorkerExecutionEvidence,
        slice_index: int,
        max_slices: int,
        elapsed_ms: int,
        previous_changed_files: tuple[str, ...] | None,
        repeated_file_slices: int,
    ) -> bool:
        checkpoint = execution.checkpoint
        if (
            execution.status is not WorkerExecutionStatus.FAILED
            or checkpoint is None
            or checkpoint.reason
            not in {
                CheckpointReason.TIME_LIMIT,
                CheckpointReason.ITERATION_LIMIT,
                CheckpointReason.TOOL_CALL_LIMIT,
            }
        ):
            return False
        if slice_index >= max_slices:
            return False
        if elapsed_ms >= int(self._continuation_total_budget_seconds * 1_000):
            return False
        if checkpoint.changed_files == previous_changed_files:
            return repeated_file_slices < self._continuation_max_repeated_file_slices
        return True

    def _checkpoint_with_slice_facts(
        self,
        *,
        execution: WorkerExecutionEvidence,
        slice_index: int,
        elapsed_ms: int,
        resume_from_commit: str | None,
        max_slices: int,
        remaining_interfaces: tuple[str, ...] = (),
    ) -> TaskCheckpoint | None:
        checkpoint = execution.checkpoint
        if checkpoint is None:
            return None
        result = execution.run_result
        verification_summary = (
            "本切片尚未执行确定性验证。"
            if result is None or not result.verifications
            else f"本切片已执行 {len(result.verifications)} 次确定性验证。"
        )
        failure_summary = (
            result.failures[0].message[:512] if result is not None and result.failures else ""
        )
        remaining_summary = (
            result.developer.final_message[:512]
            if result is not None and result.developer is not None
            else "继续完成尚未满足的任务验收条件。"
        )
        return checkpoint.model_copy(
            update={
                "slice_index": slice_index,
                "max_slices": max_slices,
                "elapsed_ms": elapsed_ms,
                "resume_from_commit": resume_from_commit,
                "completed_summary": (
                    f"本切片已保存 {len(checkpoint.changed_files)} 个受控文件的改动。"
                ),
                "remaining_summary": remaining_summary,
                "verification_summary": verification_summary,
                "failure_summary": failure_summary,
                "completed_interfaces": (),
                "remaining_interfaces": remaining_interfaces,
            }
        )

    async def _checkpoint_budget_facts(
        self, *, execution: WorkerExecutionEvidence, run_id: UUID
    ) -> WorkerExecutionEvidence:
        checkpoint = execution.checkpoint
        if checkpoint is None:
            return execution
        remaining = None
        if self._token_budget_reader is not None:
            budget = await self._token_budget_reader.snapshot(run_id)
            # A checkpoint is resumable while the Run has budget, not while a
            # historical work-package allocation happens to have headroom.
            remaining = max(
                0,
                budget.total_budget_tokens - budget.used_total_tokens - budget.reserved_tokens,
            )
            checkpoint = checkpoint.model_copy(update={"remaining_budget_tokens": remaining})
        return execution.model_copy(update={"checkpoint": checkpoint})

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((perf_counter() - started_at) * 1000))

    def _effective_max_slices(self, complexity_score: int) -> int:
        """Allocate continuation capacity from the persisted task shape, never an LLM guess."""

        if complexity_score <= 1:
            requested = 2
        elif complexity_score <= 3:
            requested = 3
        else:
            requested = self._continuation_max_slices
        return min(self._continuation_max_slices, requested)
