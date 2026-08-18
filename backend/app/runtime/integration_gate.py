from __future__ import annotations

import os
import re
import subprocess

from app.models.conflict import MergeConflictEvidence
from app.models.integration_gate import (
    HumanGateDecision,
    HumanIntegrationDecision,
    IntegrationGateSnapshot,
    IntegrationGateState,
    IntegrationPolicyRoute,
)
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.models.task import TaskContract
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_policy import (
    IntegrationConflictPolicy,
    conflict_evidence_fingerprint,
)
from app.workspace import LocalGitWorkspace

_OID_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_INTEGRATION_PREFIX = "refs/devflow/integration/"
_DECISION_PREFIX = "refs/devflow/integration-decisions/"


class IntegrationHumanGateError(RuntimeError):
    """Raised when conflict policy or durable human-gate evidence fails closed."""


class IntegrationHumanGate:
    """Route a classified conflict and durably record explicit human decisions.

    Step 2.7 never resolves a conflicted tree, deletes the conflict marker, or advances the
    integration ref. `repair_may_start=True` only authorizes a later bounded repair stage.
    """

    def __init__(
        self,
        *,
        workspace: LocalGitWorkspace,
        queue_snapshot: MergeQueueSnapshot,
        task: TaskContract,
        evidence: MergeConflictEvidence,
        policy: IntegrationConflictPolicy | None = None,
        git_timeout_seconds: float = 15.0,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if workspace.changed_files():
            raise IntegrationHumanGateError(
                "base workspace must be clean before integration-gate evaluation"
            )

        self._workspace = workspace
        self._queue_snapshot = queue_snapshot
        self._task = task
        self._evidence = evidence
        self._policy = policy or IntegrationConflictPolicy()
        self._git_timeout_seconds = git_timeout_seconds
        self._decision_ref = self._derive_decision_ref(queue_snapshot.integration_ref)
        self._validate_ref_name(self._decision_ref)

        self._validate_snapshot_binding()
        self._fingerprint = conflict_evidence_fingerprint(evidence)
        current = self._reproduce_current_evidence()
        if conflict_evidence_fingerprint(current) != self._fingerprint:
            raise IntegrationHumanGateError(
                "provided conflict evidence does not match current reproducible Git evidence"
            )

        self._policy_decision = self._policy.evaluate(evidence, task)
        if self._policy_decision.evidence_fingerprint != self._fingerprint:
            raise IntegrationHumanGateError("integration policy returned a mismatched evidence hash")
        self._human_decision = self._recover_human_decision()
        self._assert_authoritative_refs()
        self._assert_base_clean()

    @property
    def decision_ref(self) -> str:
        return self._decision_ref

    def snapshot(self) -> IntegrationGateSnapshot:
        if self._policy_decision.route is IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE:
            state = IntegrationGateState.AUTO_REPAIR_CANDIDATE
            repair_may_start = True
        elif self._human_decision is None:
            state = IntegrationGateState.AWAITING_HUMAN
            repair_may_start = False
        elif self._human_decision.decision is HumanGateDecision.AUTHORIZE_REPAIR:
            state = IntegrationGateState.REPAIR_AUTHORIZED
            repair_may_start = True
        else:
            state = IntegrationGateState.ABORTED
            repair_may_start = False

        attempt = self._terminal_attempt()
        return IntegrationGateSnapshot(
            task_id=attempt.task_id,
            task_branch=attempt.task_branch,
            task_commit=attempt.task_commit,
            integration_ref=self._queue_snapshot.integration_ref,
            integration_head=self._evidence.integration_head,
            conflict_ref=self._evidence.conflict_ref,
            conflict_marker_commit=self._evidence.marker_commit,
            evidence_fingerprint=self._fingerprint,
            policy=self._policy_decision,
            state=state,
            human_decision=self._human_decision,
            repair_may_start=repair_may_start,
            integration_may_advance=False,
        )

    def record_human_decision(
        self,
        decision: HumanGateDecision,
        *,
        actor: str,
        note: str = "",
    ) -> IntegrationGateSnapshot:
        """Record one immutable explicit human decision without changing integration state."""

        if self._policy_decision.route is not IntegrationPolicyRoute.HUMAN_REQUIRED:
            raise IntegrationHumanGateError(
                "human decision is not accepted for an automatic-repair policy candidate"
            )
        if self._human_decision is not None:
            raise IntegrationHumanGateError("a human integration decision is already recorded")

        actor = actor.strip()
        note = note.strip()
        self._validate_human_metadata(actor=actor, note=note)

        current = self._reproduce_current_evidence()
        if conflict_evidence_fingerprint(current) != self._fingerprint:
            raise IntegrationHumanGateError(
                "conflict evidence changed before the human decision could be recorded"
            )
        current_policy = self._policy.evaluate(current, self._task)
        if current_policy != self._policy_decision:
            raise IntegrationHumanGateError(
                "integration policy changed before the human decision could be recorded"
            )
        self._assert_decision_ref_absent()

        marker_tree = self._resolve_tree(self._evidence.marker_commit)
        commit = self._git(
            [
                "commit-tree",
                marker_tree,
                "-p",
                self._evidence.marker_commit,
                "-m",
                self._decision_message(decision=decision, actor=actor, note=note),
            ],
            env=self._commit_environment(),
        ).stdout.strip()
        self._require_oid(commit, label="human decision commit")

        self._create_decision_ref_transactionally(commit)
        self._human_decision = self._recover_human_decision(required=True)
        self._assert_authoritative_refs()
        self._assert_base_clean()
        return self.snapshot()

    def _validate_snapshot_binding(self) -> None:
        if not self._queue_snapshot.stopped or not self._queue_snapshot.attempts:
            raise IntegrationHumanGateError(
                "integration gate requires a stopped merge queue with terminal conflict evidence"
            )
        attempt = self._terminal_attempt()
        if attempt.outcome is not MergeAttemptOutcome.CONFLICT:
            raise IntegrationHumanGateError("terminal merge-queue attempt is not a conflict")
        if attempt.task_id != self._task.task_id:
            raise IntegrationHumanGateError(
                "task contract does not match the terminal conflicted task"
            )
        if attempt.previous_integration_commit != self._queue_snapshot.head_commit:
            raise IntegrationHumanGateError(
                "terminal conflict does not chain from the current integration head"
            )
        if self._evidence.integration_head != self._queue_snapshot.head_commit:
            raise IntegrationHumanGateError(
                "conflict evidence references a different integration head"
            )
        if self._evidence.task_commit != attempt.task_commit:
            raise IntegrationHumanGateError("conflict evidence references a different task commit")
        expected_conflict_ref = self._derive_conflict_ref(self._queue_snapshot.integration_ref)
        if self._evidence.conflict_ref != expected_conflict_ref:
            raise IntegrationHumanGateError("conflict evidence references an unexpected conflict ref")

    def _reproduce_current_evidence(self) -> MergeConflictEvidence:
        return GitMergeConflictClassifier(
            self._workspace,
            git_timeout_seconds=self._git_timeout_seconds,
        ).classify(self._queue_snapshot)

    def _recover_human_decision(
        self,
        *,
        required: bool = False,
    ) -> HumanIntegrationDecision | None:
        existing = self._git(
            ["show-ref", "--verify", "--quiet", self._decision_ref],
            check=False,
        )
        if existing.returncode == 1:
            if required:
                raise IntegrationHumanGateError("human decision ref was not durably created")
            return None
        if existing.returncode != 0:
            raise IntegrationHumanGateError("Git could not determine decision-ref existence")
        if self._policy_decision.route is not IntegrationPolicyRoute.HUMAN_REQUIRED:
            raise IntegrationHumanGateError(
                "unexpected human decision ref exists for an automatic-repair candidate"
            )

        decision_commit = self._resolve_commit(self._decision_ref, label="human decision ref")
        if self._commit_parents(decision_commit) != (self._evidence.marker_commit,):
            raise IntegrationHumanGateError(
                "human decision commit must have the exact conflict marker as its sole parent"
            )
        if self._resolve_tree(decision_commit) != self._resolve_tree(self._evidence.marker_commit):
            raise IntegrationHumanGateError(
                "human decision commit unexpectedly changes repository tree state"
            )

        metadata = self._parse_decision_metadata(decision_commit)
        expected = {
            "evidence_fingerprint": self._fingerprint,
            "policy_route": IntegrationPolicyRoute.HUMAN_REQUIRED.value,
            "conflict_marker": self._evidence.marker_commit,
            "conflict_ref": self._evidence.conflict_ref,
            "integration_head": self._evidence.integration_head,
            "task": self._task.task_id,
            "task_branch": self._terminal_attempt().task_branch,
            "task_commit": self._evidence.task_commit,
        }
        for key, expected_value in expected.items():
            if metadata[key] != expected_value:
                raise IntegrationHumanGateError(
                    f"human decision metadata does not match current conflict evidence: {key}"
                )

        try:
            decision = HumanGateDecision(metadata["decision"])
        except ValueError as exc:
            raise IntegrationHumanGateError(
                "human decision marker contains an unsupported decision"
            ) from exc

        record = HumanIntegrationDecision(
            decision=decision,
            actor=metadata["actor"],
            note=metadata["note"],
            decision_ref=self._decision_ref,
            decision_commit=decision_commit,
            evidence_fingerprint=self._fingerprint,
            conflict_marker_commit=self._evidence.marker_commit,
        )
        self._validate_human_metadata(actor=record.actor, note=record.note)
        return record

    def _create_decision_ref_transactionally(self, decision_commit: str) -> None:
        transaction = "\n".join(
            [
                "start",
                (
                    f"verify {self._queue_snapshot.integration_ref} "
                    f"{self._evidence.integration_head}"
                ),
                f"verify {self._evidence.conflict_ref} {self._evidence.marker_commit}",
                f"create {self._decision_ref} {decision_commit}",
                "prepare",
                "commit",
                "",
            ]
        )
        result = self._git_with_input(["update-ref", "--stdin"], transaction, check=False)
        if result.returncode != 0:
            raise IntegrationHumanGateError(
                "human decision could not be recorded atomically against the expected Git refs"
            )

    def _decision_message(
        self,
        *,
        decision: HumanGateDecision,
        actor: str,
        note: str,
    ) -> str:
        attempt = self._terminal_attempt()
        return (
            f"DevFlow human integration decision: {decision.value}\n\n"
            f"DevFlow-Human-Decision: {decision.value}\n"
            f"DevFlow-Decision-Actor: {actor}\n"
            f"DevFlow-Decision-Note: {note}\n"
            f"DevFlow-Evidence-Fingerprint: {self._fingerprint}\n"
            f"DevFlow-Policy-Route: {self._policy_decision.route.value}\n"
            f"DevFlow-Conflict-Marker: {self._evidence.marker_commit}\n"
            f"DevFlow-Conflict-Ref: {self._evidence.conflict_ref}\n"
            f"DevFlow-Integration-Head: {self._evidence.integration_head}\n"
            f"DevFlow-Task: {attempt.task_id}\n"
            f"DevFlow-Task-Branch: {attempt.task_branch}\n"
            f"DevFlow-Task-Commit: {attempt.task_commit}"
        )

    def _parse_decision_metadata(self, commit: str) -> dict[str, str]:
        message = self._git(["show", "-s", "--format=%B", commit]).stdout
        prefixes = {
            "DevFlow-Human-Decision: ": "decision",
            "DevFlow-Decision-Actor: ": "actor",
            "DevFlow-Decision-Note: ": "note",
            "DevFlow-Evidence-Fingerprint: ": "evidence_fingerprint",
            "DevFlow-Policy-Route: ": "policy_route",
            "DevFlow-Conflict-Marker: ": "conflict_marker",
            "DevFlow-Conflict-Ref: ": "conflict_ref",
            "DevFlow-Integration-Head: ": "integration_head",
            "DevFlow-Task: ": "task",
            "DevFlow-Task-Branch: ": "task_branch",
            "DevFlow-Task-Commit: ": "task_commit",
        }
        metadata: dict[str, str] = {}
        for line in message.splitlines():
            for prefix, key in prefixes.items():
                if not line.startswith(prefix):
                    continue
                if key in metadata:
                    raise IntegrationHumanGateError(
                        "human decision commit contains duplicate DevFlow metadata"
                    )
                metadata[key] = line[len(prefix) :].strip()
        if set(metadata) != set(prefixes.values()):
            raise IntegrationHumanGateError(
                "human decision commit is missing required DevFlow metadata"
            )
        self._require_oid(metadata["conflict_marker"], label="recorded conflict marker")
        self._require_oid(metadata["integration_head"], label="recorded integration head")
        self._require_oid(metadata["task_commit"], label="recorded task commit")
        if re.fullmatch(r"[0-9a-f]{64}", metadata["evidence_fingerprint"]) is None:
            raise IntegrationHumanGateError("recorded evidence fingerprint is malformed")
        return metadata

    def _assert_decision_ref_absent(self) -> None:
        existing = self._git(
            ["show-ref", "--verify", "--quiet", self._decision_ref],
            check=False,
        )
        if existing.returncode == 0:
            raise IntegrationHumanGateError("a human integration decision already exists")
        if existing.returncode != 1:
            raise IntegrationHumanGateError("Git could not validate decision-ref availability")

    def _assert_authoritative_refs(self) -> None:
        integration = self._resolve_commit(
            self._queue_snapshot.integration_ref,
            label="integration ref",
        )
        if integration != self._evidence.integration_head:
            raise IntegrationHumanGateError("integration ref moved outside the human gate")
        conflict = self._resolve_commit(self._evidence.conflict_ref, label="conflict ref")
        if conflict != self._evidence.marker_commit:
            raise IntegrationHumanGateError("conflict ref moved outside the human gate")

    def _terminal_attempt(self):
        return self._queue_snapshot.attempts[-1]

    @staticmethod
    def _validate_human_metadata(*, actor: str, note: str) -> None:
        if not actor or len(actor) > 128:
            raise ValueError("actor must contain between 1 and 128 characters")
        if len(note) > 512:
            raise ValueError("note must contain at most 512 characters")
        if any(value in actor or value in note for value in ("\n", "\r")):
            raise ValueError("human decision metadata must be single-line")

    @staticmethod
    def _derive_decision_ref(integration_ref: str) -> str:
        if not integration_ref.startswith(_INTEGRATION_PREFIX):
            raise IntegrationHumanGateError(
                "integration ref is outside the DevFlow integration namespace"
            )
        suffix = integration_ref[len(_INTEGRATION_PREFIX) :]
        if not suffix:
            raise IntegrationHumanGateError("integration ref has no run identity")
        return _DECISION_PREFIX + suffix

    @staticmethod
    def _derive_conflict_ref(integration_ref: str) -> str:
        if not integration_ref.startswith(_INTEGRATION_PREFIX):
            raise IntegrationHumanGateError(
                "integration ref is outside the DevFlow integration namespace"
            )
        suffix = integration_ref[len(_INTEGRATION_PREFIX) :]
        if not suffix:
            raise IntegrationHumanGateError("integration ref has no run identity")
        return f"refs/devflow/integration-conflicts/{suffix}"

    def _validate_ref_name(self, ref_name: str) -> None:
        result = self._git(["check-ref-format", ref_name], check=False)
        if result.returncode != 0:
            raise IntegrationHumanGateError("generated human-decision ref is not a valid Git ref")

    def _resolve_commit(self, ref: str, *, label: str) -> str:
        result = self._git(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        resolved = result.stdout.strip()
        if result.returncode != 0 or _OID_PATTERN.fullmatch(resolved) is None:
            raise IntegrationHumanGateError(f"{label} does not resolve to a full commit id")
        return resolved

    def _resolve_tree(self, ref: str) -> str:
        result = self._git(["rev-parse", "--verify", f"{ref}^{{tree}}"], check=False)
        resolved = result.stdout.strip()
        if result.returncode != 0 or _OID_PATTERN.fullmatch(resolved) is None:
            raise IntegrationHumanGateError("Git reference does not resolve to a full tree id")
        return resolved

    def _commit_parents(self, commit: str) -> tuple[str, ...]:
        line = self._git(["rev-list", "--parents", "-n", "1", commit]).stdout.strip()
        values = line.split()
        if not values or values[0] != commit:
            raise IntegrationHumanGateError("Git returned inconsistent decision-parent evidence")
        return tuple(values[1:])

    @staticmethod
    def _require_oid(value: str, *, label: str) -> None:
        if _OID_PATTERN.fullmatch(value) is None:
            raise IntegrationHumanGateError(f"{label} must be a full Git object id")

    @staticmethod
    def _commit_environment() -> dict[str, str]:
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "DevFlow Human Gate",
            "GIT_AUTHOR_EMAIL": "devflow-human-gate@local.invalid",
            "GIT_COMMITTER_NAME": "DevFlow Human Gate",
            "GIT_COMMITTER_EMAIL": "devflow-human-gate@local.invalid",
        }

    def _assert_base_clean(self) -> None:
        if self._workspace.changed_files():
            raise IntegrationHumanGateError(
                "integration-gate operation unexpectedly dirtied the base workspace"
            )

    def _git(
        self,
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(self._workspace.root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise IntegrationHumanGateError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise IntegrationHumanGateError(
                "git integration-gate command exceeded the configured timeout"
            ) from exc
        if check and completed.returncode != 0:
            raise IntegrationHumanGateError(
                "git integration-gate command failed: "
                f"exit_code={completed.returncode}, operation={arguments[0]}"
            )
        return completed

    def _git_with_input(
        self,
        arguments: list[str],
        input_text: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(self._workspace.root), *arguments]
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise IntegrationHumanGateError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise IntegrationHumanGateError(
                "git integration-gate transaction exceeded the configured timeout"
            ) from exc
        if check and completed.returncode != 0:
            raise IntegrationHumanGateError(
                "git integration-gate transaction failed: "
                f"exit_code={completed.returncode}, operation={arguments[0]}"
            )
        return completed
