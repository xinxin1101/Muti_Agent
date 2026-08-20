from __future__ import annotations

import asyncio
import re
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.run_reconciliation import (
    TaskExecutionBase,
    TaskExecutionBaseBasis,
)
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import PersistedEvidence, PersistedRunSnapshot, PersistenceEvidenceKind
from app.workspace import (
    CommitDiffError,
    LocalGitWorkspace,
    ReadOnlyCommitDiffReader,
    WorkspaceGitError,
)

_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_MERGE_ATTEMPTS = 1024


class TaskExecutionBaseUnavailableError(RuntimeError):
    """Raised when accepted integration evidence does not yet define a legal task base."""


class ExecutionBaseDAGReader(Protocol):
    async def load_dag(self, run_id: UUID) -> PersistedDAGSnapshot: ...


class ExecutionBaseWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class EvidenceBoundTaskExecutionBaseResolver:
    """Resolve a task Git base from immutable DAG and accepted integration evidence.

    Queue messages never carry a caller-selected base SHA. Root tasks use the frozen Run base.
    Dependent tasks require a cumulative MERGE_QUEUE_SNAPSHOT whose current integration head
    contains every direct dependency. When a managed workspace resolver is supplied, Git object
    parentage is revalidated before the base is returned.
    """

    def __init__(
        self,
        *,
        dag_reader: ExecutionBaseDAGReader,
        workspace_resolver: ExecutionBaseWorkspaceResolver | None = None,
    ) -> None:
        self._dag_reader = dag_reader
        self._workspace_resolver = workspace_resolver

    async def resolve(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> TaskExecutionBase:
        persisted_dag = await self._dag_reader.load_dag(snapshot.run_id)
        if persisted_dag.run_id != snapshot.run_id:
            raise PersistenceCorruptionError("execution-base DAG Run identity mismatch")
        base, merge_snapshot = self._select_base(
            snapshot=snapshot,
            persisted_dag=persisted_dag,
            task_id=task_id,
        )
        if self._workspace_resolver is not None:
            try:
                workspace = self._workspace_resolver.resolve(snapshot.project_id)
                await asyncio.to_thread(
                    self._verify_git,
                    workspace,
                    snapshot.base_commit,
                    merge_snapshot,
                    base.commit_sha,
                )
            except (ValueError, WorkspaceGitError, CommitDiffError) as exc:
                raise PersistenceCorruptionError(
                    "execution-base evidence cannot be reproduced from managed Git"
                ) from exc
        return base

    def _select_base(
        self,
        *,
        snapshot: PersistedRunSnapshot,
        persisted_dag: PersistedDAGSnapshot,
        task_id: str,
    ) -> tuple[TaskExecutionBase, MergeQueueSnapshot | None]:
        dag = persisted_dag.dag
        try:
            node = dag.node(task_id)
        except KeyError as exc:
            raise ValueError(f"task {task_id!r} does not belong to run {snapshot.run_id}") from exc

        if not node.depends_on:
            self._require_oid(snapshot.base_commit, label="Run base commit")
            return (
                TaskExecutionBase(
                    run_id=snapshot.run_id,
                    task_id=task_id,
                    commit_sha=snapshot.base_commit,
                    basis=TaskExecutionBaseBasis.RUN_BASE,
                ),
                None,
            )

        workers = self._successful_worker_pairs(snapshot, set(dag.task_ids))
        snapshots = self._validated_merge_snapshots(
            snapshot=snapshot,
            persisted_dag=persisted_dag,
            successful_workers=workers,
        )
        if not snapshots:
            raise TaskExecutionBaseUnavailableError(
                f"dependent task {task_id!r} has no accepted integration history"
            )

        evidence, latest = snapshots[-1]
        if latest.stopped:
            raise TaskExecutionBaseUnavailableError(
                "integration queue is stopped by unresolved conflict; "
                "downstream dispatch is blocked"
            )
        missing = set(node.depends_on) - set(latest.integrated_task_ids)
        if missing:
            raise TaskExecutionBaseUnavailableError(
                f"dependent task {task_id!r} is waiting for integrated dependencies: "
                + ", ".join(sorted(missing))
            )
        self._require_oid(latest.head_commit, label="integration head")
        return (
            TaskExecutionBase(
                run_id=snapshot.run_id,
                task_id=task_id,
                commit_sha=latest.head_commit,
                basis=TaskExecutionBaseBasis.MERGE_QUEUE_SNAPSHOT,
                source_evidence_id=evidence.id,
                source_evidence_sha256=evidence.payload_sha256,
                integration_ref=latest.integration_ref,
            ),
            latest,
        )

    @classmethod
    def _successful_worker_pairs(
        cls,
        snapshot: PersistedRunSnapshot,
        known_task_ids: set[str],
    ) -> dict[str, tuple[str, str]]:
        pairs: dict[str, tuple[str, str]] = {}
        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.WORKER_EXECUTION:
                continue
            if evidence.task_id is None or evidence.task_id not in known_task_ids:
                raise PersistenceCorruptionError(
                    "worker execution evidence is not bound to a known DAG task"
                )
            try:
                execution = WorkerExecutionEvidence.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"worker execution evidence {evidence.id} failed schema validation"
                ) from exc
            if execution.run_id != snapshot.run_id or execution.task_id != evidence.task_id:
                raise PersistenceCorruptionError(
                    f"worker execution evidence {evidence.id} has mismatched identity"
                )
            if execution.status is not WorkerExecutionStatus.SUCCEEDED:
                continue
            if execution.commit_sha is None:
                raise PersistenceCorruptionError("successful worker evidence lacks task commit")
            cls._require_oid(execution.base_commit, label="worker task base")
            cls._require_oid(execution.commit_sha, label="worker task commit")
            pair = (execution.base_commit, execution.commit_sha)
            existing = pairs.get(execution.task_id)
            if existing is not None and existing != pair:
                raise PersistenceCorruptionError(
                    "successful worker evidence defines conflicting commits for "
                    f"{execution.task_id!r}"
                )
            pairs[execution.task_id] = pair
        return pairs

    @classmethod
    def _validated_merge_snapshots(
        cls,
        *,
        snapshot: PersistedRunSnapshot,
        persisted_dag: PersistedDAGSnapshot,
        successful_workers: dict[str, tuple[str, str]],
    ) -> tuple[tuple[PersistedEvidence, MergeQueueSnapshot], ...]:
        dag = persisted_dag.dag
        order_index = {task_id: index for index, task_id in enumerate(dag.topological_order())}
        decoded: list[tuple[PersistedEvidence, MergeQueueSnapshot]] = []

        for evidence in snapshot.evidence:
            if evidence.kind is not PersistenceEvidenceKind.MERGE_QUEUE_SNAPSHOT:
                continue
            try:
                merge_snapshot = MergeQueueSnapshot.model_validate(evidence.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"merge queue evidence {evidence.id} failed schema validation"
                ) from exc
            if merge_snapshot.run_base_commit != snapshot.base_commit:
                raise PersistenceCorruptionError(
                    "merge queue snapshot does not match the persisted Run base"
                )
            if len(merge_snapshot.attempts) > _MAX_MERGE_ATTEMPTS:
                raise PersistenceCorruptionError(
                    "merge queue evidence exceeds bounded attempt scan"
                )
            cls._validate_merge_history(
                merge_snapshot=merge_snapshot,
                dag=persisted_dag.dag,
                order_index=order_index,
                successful_workers=successful_workers,
                run_base_commit=snapshot.base_commit,
            )
            decoded.append((evidence, merge_snapshot))

        previous: MergeQueueSnapshot | None = None
        for _, current in decoded:
            if previous is None:
                previous = current
                continue
            if current.integration_ref != previous.integration_ref:
                raise PersistenceCorruptionError(
                    "one Run contains conflicting integration-ref histories"
                )
            if len(current.attempts) < len(previous.attempts):
                raise PersistenceCorruptionError("merge queue snapshot history regressed")
            if current.attempts[: len(previous.attempts)] != previous.attempts:
                raise PersistenceCorruptionError("merge queue snapshot history diverged")
            if len(current.attempts) == len(previous.attempts) and current != previous:
                raise PersistenceCorruptionError(
                    "merge queue snapshot changed without extending its attempt history"
                )
            if previous.stopped and current != previous:
                raise PersistenceCorruptionError(
                    "merge queue history advanced after an unresolved terminal conflict"
                )
            previous = current
        return tuple(decoded)

    @classmethod
    def _validate_merge_history(
        cls,
        *,
        merge_snapshot: MergeQueueSnapshot,
        dag,
        order_index: dict[str, int],
        successful_workers: dict[str, tuple[str, str]],
        run_base_commit: str,
    ) -> None:
        integrated: set[str] = set()
        heads: dict[str, frozenset[str]] = {run_base_commit: frozenset()}
        last_index = -1

        for attempt in merge_snapshot.attempts:
            cls._require_oid(attempt.task_base_commit, label="merge task base")
            cls._require_oid(attempt.task_commit, label="merge task commit")
            cls._require_oid(
                attempt.previous_integration_commit,
                label="previous integration commit",
            )
            if attempt.task_id not in order_index:
                raise PersistenceCorruptionError(
                    "merge queue snapshot references an unknown DAG task"
                )
            index = order_index[attempt.task_id]
            if index <= last_index:
                raise PersistenceCorruptionError(
                    "merge queue snapshot violates deterministic topological task order"
                )
            last_index = index

            node = dag.node(attempt.task_id)
            missing_dependencies = set(node.depends_on) - integrated
            if missing_dependencies:
                raise PersistenceCorruptionError(
                    f"merge attempt for {attempt.task_id!r} precedes integrated dependencies"
                )

            worker_pair = successful_workers.get(attempt.task_id)
            if worker_pair != (attempt.task_base_commit, attempt.task_commit):
                raise PersistenceCorruptionError(
                    f"merge attempt for {attempt.task_id!r} lacks matching successful "
                    "worker evidence"
                )

            if not node.depends_on:
                if attempt.task_base_commit != run_base_commit:
                    raise PersistenceCorruptionError(
                        "dependency-free task commit must descend from the frozen Run base"
                    )
            else:
                base_integrated = heads.get(attempt.task_base_commit)
                if base_integrated is None or not set(node.depends_on).issubset(base_integrated):
                    raise PersistenceCorruptionError(
                        "dependent task base is not a prior integration head "
                        "containing its dependencies"
                    )

            if attempt.outcome is MergeAttemptOutcome.INTEGRATED:
                if attempt.integration_commit is None:
                    raise PersistenceCorruptionError(
                        "integrated merge attempt lacks integration commit"
                    )
                cls._require_oid(attempt.integration_commit, label="integration commit")
                integrated.add(attempt.task_id)
                heads[attempt.integration_commit] = frozenset(integrated)

    @staticmethod
    def _verify_git(
        workspace: LocalGitWorkspace,
        run_base_commit: str,
        merge_snapshot: MergeQueueSnapshot | None,
        selected_commit: str,
    ) -> None:
        reader = ReadOnlyCommitDiffReader(workspace)
        reader.commit_parents(run_base_commit)
        if merge_snapshot is not None:
            for attempt in merge_snapshot.attempts:
                task_parents = reader.commit_parents(attempt.task_commit)
                if task_parents != (attempt.task_base_commit,):
                    raise PersistenceCorruptionError(
                        "persisted task commit no longer matches its evidence-bound task base"
                    )
                if attempt.outcome is MergeAttemptOutcome.INTEGRATED:
                    assert attempt.integration_commit is not None
                    integration_parents = reader.commit_parents(attempt.integration_commit)
                    if integration_parents != (
                        attempt.previous_integration_commit,
                        attempt.task_commit,
                    ):
                        raise PersistenceCorruptionError(
                            "persisted integration commit no longer matches its parent evidence"
                        )
        reader.commit_parents(selected_commit)

    @staticmethod
    def _require_oid(value: str, *, label: str) -> None:
        if _OID_RE.fullmatch(value) is None:
            raise PersistenceCorruptionError(f"{label} is not a full lowercase Git object id")
