from __future__ import annotations

from pydantic import ValidationError

from app.models.integration_gate import HumanGateDecision, HumanIntegrationDecision
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.persistence.dag import PersistedDAGSnapshot
from app.persistence.errors import PersistenceCorruptionError
from app.persistence.types import PersistedEvidence, PersistedRunSnapshot, PersistenceEvidenceKind
from app.runtime.execution_base import (
    _MAX_MERGE_ATTEMPTS,
    EvidenceBoundTaskExecutionBaseResolver,
)
from app.workspace import LocalGitWorkspace, ReadOnlyCommitDiffReader


class RepairAwareEvidenceBoundTaskExecutionBaseResolver(EvidenceBoundTaskExecutionBaseResolver):
    """Step 5.4 resolver extended only for accepted REPAIRED integration evidence."""

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
        repairs = cls._repair_evidence(snapshot)
        decisions = cls._repair_decisions(snapshot)
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
            cls._validate_repair_aware_history(
                merge_snapshot=merge_snapshot,
                dag=dag,
                order_index=order_index,
                successful_workers=successful_workers,
                run_base_commit=snapshot.base_commit,
                repairs=repairs,
                decisions=decisions,
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
                cls._assert_stopped_history_resumed_by_repair(previous, current)
            previous = current
        return tuple(decoded)

    @classmethod
    def _validate_repair_aware_history(
        cls,
        *,
        merge_snapshot: MergeQueueSnapshot,
        dag,
        order_index: dict[str, int],
        successful_workers: dict[str, tuple[str, str]],
        run_base_commit: str,
        repairs: dict[tuple[str, str], IntegrationConflictRepairEvidence],
        decisions: set[tuple[str, str, str]],
    ) -> None:
        integrated: set[str] = set()
        heads: dict[str, frozenset[str]] = {run_base_commit: frozenset()}
        last_index = -1
        pending_conflict = None

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
            node = dag.node(attempt.task_id)

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
                        "dependent task base is not a prior integration head containing "
                        "dependencies"
                    )
            if not set(node.depends_on).issubset(integrated):
                raise PersistenceCorruptionError(
                    f"merge attempt for {attempt.task_id!r} precedes integrated dependencies"
                )

            if attempt.outcome is MergeAttemptOutcome.CONFLICT:
                if pending_conflict is not None or index <= last_index:
                    raise PersistenceCorruptionError(
                        "merge conflict violates deterministic topological ordering"
                    )
                pending_conflict = attempt
                continue

            if attempt.outcome is MergeAttemptOutcome.REPAIRED:
                if pending_conflict is None or pending_conflict.task_id != attempt.task_id:
                    raise PersistenceCorruptionError(
                        "repaired merge attempt lacks its immediately preceding conflict"
                    )
                if index <= last_index or index != order_index[pending_conflict.task_id]:
                    raise PersistenceCorruptionError(
                        "repaired merge attempt violates deterministic topological ordering"
                    )
                if (
                    attempt.previous_integration_commit
                    != pending_conflict.previous_integration_commit
                ):
                    raise PersistenceCorruptionError(
                        "repair does not chain from the conflicted integration head"
                    )
                if (
                    attempt.integration_commit is None
                    or attempt.conflict_evidence_fingerprint is None
                ):
                    raise PersistenceCorruptionError("repaired merge attempt lacks repair identity")
                repair = repairs.get((attempt.task_id, attempt.conflict_evidence_fingerprint))
                if repair is None:
                    raise PersistenceCorruptionError(
                        "repaired merge attempt lacks matching typed integration-repair evidence"
                    )
                cls._validate_repair_binding(
                    attempt=attempt,
                    repair=repair,
                    decisions=decisions,
                )
                integrated.add(attempt.task_id)
                heads[attempt.integration_commit] = frozenset(integrated)
                last_index = index
                pending_conflict = None
                continue

            if pending_conflict is not None:
                raise PersistenceCorruptionError(
                    "naturally clean integration cannot skip an unresolved prior conflict"
                )
            if index <= last_index:
                raise PersistenceCorruptionError(
                    "merge queue snapshot violates deterministic topological task order"
                )
            if attempt.integration_commit is None:
                raise PersistenceCorruptionError(
                    "integrated merge attempt lacks integration commit"
                )
            integrated.add(attempt.task_id)
            heads[attempt.integration_commit] = frozenset(integrated)
            last_index = index

    @staticmethod
    def _validate_repair_binding(
        *,
        attempt,
        repair: IntegrationConflictRepairEvidence,
        decisions: set[tuple[str, str, str]],
    ) -> None:
        expected = {
            "task_id": attempt.task_id,
            "integration_head": attempt.previous_integration_commit,
            "task_commit": attempt.task_commit,
            "repair_commit": attempt.integration_commit,
            "conflict_marker_commit": attempt.conflict_marker_commit,
            "conflict_evidence_fingerprint": attempt.conflict_evidence_fingerprint,
            "policy_fingerprint": attempt.policy_fingerprint,
            "human_decision_commit": attempt.human_decision_commit,
        }
        for field, value in expected.items():
            if getattr(repair, field) != value:
                raise PersistenceCorruptionError(
                    f"integration repair evidence binding changed: {field}"
                )
        decision_key = (
            attempt.task_id,
            repair.conflict_evidence_fingerprint,
            repair.human_decision_commit,
        )
        if decision_key not in decisions:
            raise PersistenceCorruptionError(
                "integration repair lacks matching AUTHORIZE_REPAIR human decision evidence"
            )

    @staticmethod
    def _repair_evidence(
        snapshot: PersistedRunSnapshot,
    ) -> dict[tuple[str, str], IntegrationConflictRepairEvidence]:
        repairs: dict[tuple[str, str], IntegrationConflictRepairEvidence] = {}
        task_ids = {item.task.task_id for item in snapshot.tasks}
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.INTEGRATION_REPAIR:
                continue
            try:
                repair = IntegrationConflictRepairEvidence.model_validate(item.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"integration repair evidence {item.id} failed schema validation"
                ) from exc
            if repair.run_id != snapshot.run_id or repair.task_id not in task_ids:
                raise PersistenceCorruptionError("integration repair evidence identity mismatch")
            key = (repair.task_id, repair.conflict_evidence_fingerprint)
            existing = repairs.get(key)
            if existing is not None and existing != repair:
                raise PersistenceCorruptionError(
                    "one conflict contains conflicting integration repair evidence"
                )
            repairs[key] = repair
        return repairs

    @staticmethod
    def _repair_decisions(snapshot: PersistedRunSnapshot) -> set[tuple[str, str, str]]:
        decisions: set[tuple[str, str, str]] = set()
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.HUMAN_DECISION or item.task_id is None:
                continue
            try:
                decision = HumanIntegrationDecision.model_validate(item.payload)
            except ValidationError as exc:
                raise PersistenceCorruptionError(
                    f"human decision evidence {item.id} failed schema validation"
                ) from exc
            if decision.decision is HumanGateDecision.AUTHORIZE_REPAIR:
                decisions.add(
                    (
                        item.task_id,
                        decision.evidence_fingerprint,
                        decision.decision_commit,
                    )
                )
        return decisions

    @staticmethod
    def _assert_stopped_history_resumed_by_repair(
        previous: MergeQueueSnapshot,
        current: MergeQueueSnapshot,
    ) -> None:
        if not previous.attempts or len(current.attempts) <= len(previous.attempts):
            raise PersistenceCorruptionError(
                "stopped merge queue history advanced without a repair attempt"
            )
        conflict = previous.attempts[-1]
        first_new = current.attempts[len(previous.attempts)]
        if (
            conflict.outcome is not MergeAttemptOutcome.CONFLICT
            or first_new.outcome is not MergeAttemptOutcome.REPAIRED
            or first_new.task_id != conflict.task_id
            or first_new.previous_integration_commit != conflict.previous_integration_commit
        ):
            raise PersistenceCorruptionError(
                "stopped merge queue history resumed without resolving its exact conflict"
            )

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
                if attempt.outcome in {
                    MergeAttemptOutcome.INTEGRATED,
                    MergeAttemptOutcome.REPAIRED,
                }:
                    if attempt.integration_commit is None:
                        raise PersistenceCorruptionError(
                            "successful merge attempt lacks integration commit"
                        )
                    integration_parents = reader.commit_parents(attempt.integration_commit)
                    if integration_parents != (
                        attempt.previous_integration_commit,
                        attempt.task_commit,
                    ):
                        raise PersistenceCorruptionError(
                            "persisted integration commit no longer matches its parent evidence"
                        )
        reader.commit_parents(selected_commit)
