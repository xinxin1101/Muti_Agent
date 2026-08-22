from __future__ import annotations

from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt
from app.runtime.merge_queue import MergeQueueError, TopologicalMergeQueue

_REPAIR_FLAG = "DevFlow-Integration-Repair: true"
_REPAIR_PREFIXES = {
    "DevFlow-Conflict-Marker: ": "conflict_marker",
    "DevFlow-Conflict-Evidence-Fingerprint: ": "evidence_fingerprint",
    "DevFlow-Policy-Fingerprint: ": "policy_fingerprint",
    "DevFlow-Human-Decision-Commit: ": "human_decision_commit",
}


class RepairAwareTopologicalMergeQueue(TopologicalMergeQueue):
    """Phase 6 merge queue that can recover explicitly repaired conflict history.

    Phase 2 clean integrations remain byte-for-byte governed by TopologicalMergeQueue. The only
    additional accepted history is a two-parent integration commit carrying explicit repair
    metadata and backed by a reproducible prior Git conflict. A repaired commit is never treated as
    a naturally clean merge.
    """

    def _recover_successful_history(self, head: str) -> None:
        if head == self._run_base:
            return
        commits = [
            value
            for value in self._git(
                ["rev-list", "--first-parent", "--reverse", f"{self._run_base}..{head}"]
            ).stdout.splitlines()
            if value
        ]
        expected_previous = self._run_base
        recovered: set[str] = set()
        last_index = -1

        for commit in commits:
            parents = self._commit_parents(commit)
            if len(parents) != 2 or parents[0] != expected_previous:
                raise MergeQueueError("existing integration history has an unexpected parent chain")
            metadata = self._parse_integration_message(commit)
            task_id = metadata["task"]
            task_branch = metadata["branch"]
            task_base = metadata["base"]
            task_commit = metadata["commit"]
            if task_id not in self._order_index:
                raise MergeQueueError("existing integration history references an unknown DAG task")
            if task_id in recovered:
                raise MergeQueueError(
                    "existing integration history integrates a task more than once"
                )
            if self._order_index[task_id] <= last_index:
                raise MergeQueueError(
                    "existing integration history violates deterministic DAG order"
                )
            if parents[1] != task_commit:
                raise MergeQueueError(
                    "existing integration history task parent does not match metadata"
                )

            record = self._worktrees.record_for(task_id, base_commit=task_base)
            if task_branch != record.branch_name:
                raise MergeQueueError(
                    "existing integration history records an unexpected task branch"
                )
            if self._commit_parents(task_commit) != (task_base,):
                raise MergeQueueError("recovered task commit does not match its recorded task base")
            node = self._scheduler.dag.node(task_id)
            if not set(node.depends_on).issubset(recovered):
                raise MergeQueueError("existing integration history violates task dependencies")

            repair = self._parse_repair_metadata(commit)
            if repair is None:
                self._verify_integration_commit(
                    commit=commit,
                    previous_head=expected_previous,
                    task_commit=task_commit,
                )
                self._attempts.append(
                    MergeQueueAttempt(
                        sequence=len(self._attempts),
                        task_id=task_id,
                        task_branch=task_branch,
                        task_base_commit=task_base,
                        task_commit=task_commit,
                        previous_integration_commit=expected_previous,
                        outcome=MergeAttemptOutcome.INTEGRATED,
                        integration_commit=commit,
                    )
                )
            else:
                self._recover_repaired_attempt(
                    commit=commit,
                    task_id=task_id,
                    task_branch=task_branch,
                    task_base=task_base,
                    task_commit=task_commit,
                    previous_head=expected_previous,
                    repair=repair,
                )

            self._integrated.append(task_id)
            recovered.add(task_id)
            last_index = self._order_index[task_id]
            expected_previous = commit

        if expected_previous != head:
            raise MergeQueueError("existing integration history could not recover its final head")

    def _recover_repaired_attempt(
        self,
        *,
        commit: str,
        task_id: str,
        task_branch: str,
        task_base: str,
        task_commit: str,
        previous_head: str,
        repair: dict[str, str],
    ) -> None:
        merge = self._git(
            ["merge-tree", "--write-tree", previous_head, task_commit],
            check=False,
        )
        if merge.returncode != 1:
            raise MergeQueueError(
                "repair-marked integration no longer reproduces the conflict it claims to repair"
            )

        marker = repair["conflict_marker"]
        self._require_full_oid(marker, label="repair conflict marker")
        if self._commit_parents(marker) != (previous_head,):
            raise MergeQueueError("repair conflict marker has an unexpected parent")
        if self._resolve_tree(marker) != self._resolve_tree(previous_head):
            raise MergeQueueError("repair conflict marker unexpectedly changes repository tree")
        marker_metadata = self._parse_conflict_message(marker)
        expected_marker = {
            "task": task_id,
            "branch": task_branch,
            "base": task_base,
            "commit": task_commit,
            "integration_head": previous_head,
        }
        for key, expected in expected_marker.items():
            if marker_metadata[key] != expected:
                raise MergeQueueError(f"repair conflict marker metadata mismatch: {key}")

        self._require_sha256(repair["evidence_fingerprint"], label="conflict evidence fingerprint")
        self._require_sha256(repair["policy_fingerprint"], label="policy fingerprint")
        decision_commit = repair["human_decision_commit"]
        self._require_full_oid(decision_commit, label="human decision commit")
        if self._commit_parents(decision_commit) != (marker,):
            raise MergeQueueError(
                "repair human decision commit is not bound to the conflict marker"
            )
        if self._resolve_tree(decision_commit) != self._resolve_tree(marker):
            raise MergeQueueError("repair human decision commit unexpectedly changes tree state")

        self._attempts.append(
            MergeQueueAttempt(
                sequence=len(self._attempts),
                task_id=task_id,
                task_branch=task_branch,
                task_base_commit=task_base,
                task_commit=task_commit,
                previous_integration_commit=previous_head,
                outcome=MergeAttemptOutcome.CONFLICT,
                failure=self._merge_conflict_failure(
                    task_id=task_id,
                    integration_head=previous_head,
                    task_commit=task_commit,
                    conflict_ref=self._conflict_ref,
                    marker_commit=marker,
                    stdout=merge.stdout,
                    stderr=merge.stderr,
                ),
            )
        )
        self._attempts.append(
            MergeQueueAttempt(
                sequence=len(self._attempts),
                task_id=task_id,
                task_branch=task_branch,
                task_base_commit=task_base,
                task_commit=task_commit,
                previous_integration_commit=previous_head,
                outcome=MergeAttemptOutcome.REPAIRED,
                integration_commit=commit,
                conflict_marker_commit=marker,
                conflict_evidence_fingerprint=repair["evidence_fingerprint"],
                policy_fingerprint=repair["policy_fingerprint"],
                human_decision_commit=decision_commit,
            )
        )

    def _recover_conflict_marker(self) -> None:
        existing = self._git(
            ["show-ref", "--verify", "--quiet", self._conflict_ref],
            check=False,
        )
        if existing.returncode == 1:
            return
        if existing.returncode != 0:
            raise MergeQueueError("Git could not determine integration-conflict ref existence")

        marker = self._resolve_commit(self._conflict_ref, label="integration conflict marker")
        repaired = next(
            (
                attempt
                for attempt in reversed(self._attempts)
                if attempt.outcome is MergeAttemptOutcome.REPAIRED
                and attempt.conflict_marker_commit == marker
            ),
            None,
        )
        if repaired is not None:
            if self._commit_parents(marker) != (repaired.previous_integration_commit,):
                raise MergeQueueError("resolved conflict marker parent changed after repair")
            if self._resolve_tree(marker) != self._resolve_tree(
                repaired.previous_integration_commit
            ):
                raise MergeQueueError("resolved conflict marker tree changed after repair")
            return

        super()._recover_conflict_marker()

    def _parse_repair_metadata(self, commit: str) -> dict[str, str] | None:
        message = self._git(["show", "-s", "--format=%B", commit]).stdout
        lines = message.splitlines()
        if _REPAIR_FLAG not in lines:
            if any(any(line.startswith(prefix) for prefix in _REPAIR_PREFIXES) for line in lines):
                raise MergeQueueError("integration commit contains incomplete repair metadata")
            return None

        metadata: dict[str, str] = {}
        for line in lines:
            for prefix, key in _REPAIR_PREFIXES.items():
                if not line.startswith(prefix):
                    continue
                if key in metadata:
                    raise MergeQueueError("integration repair commit contains duplicate metadata")
                metadata[key] = line[len(prefix) :].strip()
        if set(metadata) != set(_REPAIR_PREFIXES.values()):
            raise MergeQueueError("integration repair commit is missing required metadata")
        return metadata

    @staticmethod
    def _require_sha256(value: str, *, label: str) -> None:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise MergeQueueError(f"{label} is not a lowercase SHA-256 digest")
