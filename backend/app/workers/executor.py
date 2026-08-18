from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from app.dispatch.errors import WorkerExecutionBoundaryError
from app.models.dispatch import (
    TaskDispatchEnvelope,
    WorkerDispatchEvent,
    WorkerDispatchPhase,
    WorkerExecutionEvidence,
    WorkerExecutionStatus,
)
from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.run import SingleTaskRunResult, TaskRunState
from app.models.task import TaskContract
from app.persistence import PersistenceEvidenceKind
from app.persistence.types import PersistedRunSnapshot, PersistedRunStatus
from app.workspace import LocalGitWorkspace, TaskWorktreeManager, TaskWorktreeRecord


class SingleTaskRunner(Protocol):
    async def run(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> SingleTaskRunResult: ...


class ProjectWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class QueuedTaskExecutionBackend(Protocol):
    async def execute(
        self,
        *,
        task: TaskContract,
        project_id: UUID,
        run_id: UUID,
        dispatch_id: UUID,
        base_commit: str,
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
    ) -> int: ...

    async def finalize_single_task_run(
        self,
        *,
        run_id: UUID,
        result: SingleTaskRunResult,
    ) -> None: ...


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
    """Execute one persisted task through the accepted worktree + single-task runtime."""

    def __init__(
        self,
        *,
        workspace_resolver: ProjectWorkspaceResolver,
        worktree_root: str | Path,
        runner_factory: Callable[[TaskContract], SingleTaskRunner],
    ) -> None:
        root = Path(worktree_root).expanduser()
        if root.exists() and root.is_symlink():
            raise ValueError("queued-task worktree root must not be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        self._workspace_resolver = workspace_resolver
        self._worktree_root = root.resolve()
        self._runner_factory = runner_factory

    async def execute(
        self,
        *,
        task: TaskContract,
        project_id: UUID,
        run_id: UUID,
        dispatch_id: UUID,
        base_commit: str,
    ) -> WorkerExecutionEvidence:
        started_at = perf_counter()
        record: TaskWorktreeRecord | None = None
        run_result: SingleTaskRunResult | None = None
        worktree_identity = self._worktree_identity(run_id, task.task_id)
        try:
            base_workspace = self._workspace_resolver.resolve(project_id)
            worktrees = TaskWorktreeManager(
                base_workspace,
                self._worktree_root / str(run_id),
            )
            record = worktrees.create(worktree_identity, base_commit=base_commit)
            workspace = worktrees.open_workspace(worktree_identity)

            runner = self._runner_factory(task)
            run_result = await runner.run(task, workspace=workspace)
            if run_result.status is TaskRunState.FAILED:
                return self._failure_evidence(
                    task=task,
                    run_id=run_id,
                    dispatch_id=dispatch_id,
                    base_commit=base_commit,
                    started_at=started_at,
                    record=record,
                    run_result=run_result,
                    failures=tuple(run_result.failures),
                )
            if run_result.status is not TaskRunState.SUCCEEDED:
                raise RuntimeError("single-task runtime returned a non-terminal status")

            commit_sha = worktrees.commit_task_changes(worktree_identity)
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
                "Queued worker execution failed before successful task-branch finalization.",
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
            )

    @staticmethod
    def _worktree_identity(run_id: UUID, task_id: str) -> str:
        task_digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]
        return f"run-{run_id.hex}-task-{task_digest}"

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
    ) -> WorkerExecutionEvidence:
        return WorkerExecutionEvidence(
            dispatch_id=dispatch_id,
            run_id=run_id,
            task_id=task.task_id,
            status=WorkerExecutionStatus.FAILED,
            base_commit=base_commit,
            branch_name=record.branch_name if record is not None else None,
            commit_sha=None,
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
    """Reload persisted identity, execute existing runtime, and persist typed worker evidence."""

    def __init__(
        self,
        *,
        store: WorkerEvidenceStore,
        backend: QueuedTaskExecutionBackend,
    ) -> None:
        self._store = store
        self._backend = backend

    async def execute(self, envelope: TaskDispatchEnvelope) -> WorkerExecutionEvidence:
        snapshot = await self._store.load_run(envelope.run_id)
        if snapshot.status is not PersistedRunStatus.RUNNING:
            raise WorkerExecutionBoundaryError("worker may execute only persisted RUNNING runs")

        task = self._task_from_snapshot(snapshot, envelope.task_id)
        await self._record_dispatch_event(
            envelope,
            phase=WorkerDispatchPhase.RECEIVED,
        )

        raw_execution = await self._backend.execute(
            task=task,
            project_id=snapshot.project_id,
            run_id=snapshot.run_id,
            dispatch_id=envelope.dispatch_id,
            base_commit=snapshot.base_commit,
        )
        execution = raw_execution
        await self._persist_runtime_result(envelope, execution.run_result)
        await self._store.append_evidence(
            run_id=envelope.run_id,
            task_id=envelope.task_id,
            evidence_key=f"dispatch:{envelope.dispatch_id}:execution",
            kind=PersistenceEvidenceKind.WORKER_EXECUTION,
            payload_model=execution,
            stage="worker",
        )
        await self._record_dispatch_event(
            envelope,
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
            )
        return execution

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
        )

    async def _persist_runtime_result(
        self,
        envelope: TaskDispatchEnvelope,
        result: SingleTaskRunResult | None,
    ) -> None:
        if result is None:
            return

        prefix = f"dispatch:{envelope.dispatch_id}"
        for event in result.events:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:state:{event.sequence:04d}",
                kind=PersistenceEvidenceKind.STATE_TRANSITION,
                payload_model=event,
                stage="runtime",
                sequence=event.sequence,
            )

        if result.developer is not None:
            await self._store.append_evidence(
                run_id=envelope.run_id,
                task_id=envelope.task_id,
                evidence_key=f"{prefix}:developer",
                kind=PersistenceEvidenceKind.DEVELOPER_RUN,
                payload_model=result.developer,
                stage="developer",
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
            )
