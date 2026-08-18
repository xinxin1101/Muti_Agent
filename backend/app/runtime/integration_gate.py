from __future__ import annotations

import json
import os
import re
import subprocess

from app.models.conflict import MergeConflictEvidence
from app.models.integration_gate import (
    HumanGateDecision,
    HumanIntegrationDecision,
    IntegrationGateSnapshot,
    IntegrationGateState,
    IntegrationPolicyDecision,
    IntegrationPolicyRoute,
)
from app.models.merge import MergeAttemptOutcome, MergeQueueAttempt, MergeQueueSnapshot
from app.models.scheduler import TaskScheduleState
from app.runtime.conflict_classifier import GitMergeConflictClassifier
from app.runtime.integration_policy import (
    IntegrationConflictPolicy,
    conflict_evidence_fingerprint,
    integration_policy_fingerprint,
)
from app.runtime.scheduler import DAGScheduler
from app.workspace import LocalGitWorkspace

_OID_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INTEGRATION_PREFIX = "refs/devflow/integration/"
_DECISION_PREFIX = "refs/devflow/integration-decisions/"
_MAX_AUTO_REPAIR_BLOB_BYTES = 512_000


class IntegrationHumanGateError(RuntimeError):
    """Raised when conflict policy or durable human-gate evidence fails closed."""


class IntegrationHumanGate:
    """Route classified conflicts and record explicit, tree-neutral human decisions.

    `repair_may_start=True` authorizes only a later bounded repair stage. Step 2.7 never
    resolves conflict content, deletes the conflict marker, or advances the integration ref.
    """

    def __init__(
        self,
        *,
        workspace: LocalGitWorkspace,
        queue_snapshot: MergeQueueSnapshot,
        scheduler: DAGScheduler,
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
        self._scheduler = scheduler
        self._evidence = evidence
        self._policy = policy or IntegrationConflictPolicy()
        self._git_timeout_seconds = git_timeout_seconds
        self._decision_ref = self._derive_decision_ref(queue_snapshot.integration_ref)
        self._validate_ref_name(self._decision_ref)

        self._validate_snapshot_binding()
        attempt = self._terminal_attempt()
        self._task = scheduler.dag.node(attempt.task_id).task
        self._evidence_fingerprint = conflict_evidence_fingerprint(evidence)
        self._assert_current_evidence()

        self._policy_decision = self._evaluate_policy(evidence)
        if self._policy_decision.evidence_fingerprint != self._evidence_fingerprint:
            raise IntegrationHumanGateError(
                "integration policy returned a mismatched evidence fingerprint"
            )
        self._policy_fingerprint = integration_policy_fingerprint(self._policy_decision)
        self._recover_human_decision()
        self._assert_authoritative_refs()
        self._assert_base_clean()

    @property
    def decision_ref(self) -> str:
        return self._decision_ref

    def snapshot(self) -> IntegrationGateSnapshot:
        """Return a live-validated gate snapshot rather than cached authorization state."""

        current_evidence = self._assert_current_evidence()
        current_policy = self._evaluate_policy(current_evidence)
        if current_policy != self._policy_decision:
            raise IntegrationHumanGateError(
                "integration policy changed after gate construction"
            )
        if integration_policy_fingerprint(current_policy) != self._policy_fingerprint:
            raise IntegrationHumanGateError(
                "integration policy fingerprint changed after gate construction"
            )
        human_decision = self._recover_human_decision()
        self._assert_authoritative_refs()
        self._assert_base_clean()

        if self._policy_decision.route is IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE:
            state = IntegrationGateState.AUTO_REPAIR_CANDIDATE
            repair_may_start = True
        elif human_decision is None:
            state = IntegrationGateState.AWAITING_HUMAN
            repair_may_start = False
        elif human_decision.decision is HumanGateDecision.AUTHORIZE_REPAIR:
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
            evidence_fingerprint=self._evidence_fingerprint,
            policy_fingerprint=self._policy_fingerprint,
            policy=self._policy_decision,
            state=state,
            human_decision=human_decision,
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
        if (
            decision is HumanGateDecision.AUTHORIZE_REPAIR
            and not self._policy_decision.human_repair_authorizable
        ):
            raise IntegrationHumanGateError(
                "human authorization cannot bypass hard Agent Repair boundaries"
            )
        if self._recover_human_decision() is not None:
            raise IntegrationHumanGateError("a human integration decision is already recorded")

        actor = actor.strip()
        note = note.strip()
        self._validate_human_metadata(actor=actor, note=note)

        current_evidence = self._assert_current_evidence()
        current_policy = self._evaluate_policy(current_evidence)
        if current_policy != self._policy_decision:
            raise IntegrationHumanGateError(
                "integration policy changed before the human decision could be recorded"
            )
        if integration_policy_fingerprint(current_policy) != self._policy_fingerprint:
            raise IntegrationHumanGateError(
                "policy fingerprint changed before the human decision could be recorded"
            )
        self._assert_decision_ref_absent()

        marker_tree = self._resolve_tree(self._evidence.marker_commit)
        decision_commit = self._git(
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
        self._require_oid(decision_commit, label="human decision commit")

        self._create_decision_ref_transactionally(decision_commit)
        recovered = self._recover_human_decision(required=True)
        if recovered is None:
            raise IntegrationHumanGateError("human decision was not durably recoverable")
        self._assert_authoritative_refs()
        self._assert_base_clean()
        return self.snapshot()

    def _evaluate_policy(self, evidence: MergeConflictEvidence) -> IntegrationPolicyDecision:
        return self._policy.evaluate(
            evidence,
            self._task,
            blob_is_safe_text=self._blob_is_safe_text,
        )

    def _validate_snapshot_binding(self) -> None:
        if not self._queue_snapshot.stopped or not self._queue_snapshot.attempts:
            raise IntegrationHumanGateError(
                "integration gate requires a stopped merge queue with terminal conflict evidence"
            )
        attempt = self._terminal_attempt()
        if attempt.outcome is not MergeAttemptOutcome.CONFLICT:
            raise IntegrationHumanGateError("terminal merge-queue attempt is not a conflict")
        try:
            self._scheduler.dag.node(attempt.task_id)
        except KeyError as exc:
            raise IntegrationHumanGateError(
                "terminal conflicted task is not present in the trusted scheduler DAG"
            ) from exc
        if self._scheduler.state(attempt.task_id) is not TaskScheduleState.SUCCEEDED:
            raise IntegrationHumanGateError(
                "terminal conflicted task must remain SUCCEEDED in the trusted scheduler"
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
            raise IntegrationHumanGateError(
                "conflict evidence references an unexpected conflict ref"
            )

    def _assert_current_evidence(self) -> MergeConflictEvidence:
        current_state = self._scheduler.state(self._terminal_attempt().task_id)
        if current_state is not TaskScheduleState.SUCCEEDED:
            raise IntegrationHumanGateError(
                "conflicted task scheduler state changed after gate construction"
            )
        current = GitMergeConflictClassifier(
            self._workspace,
            git_timeout_seconds=self._git_timeout_seconds,
        ).classify(self._queue_snapshot)
        if conflict_evidence_fingerprint(current) != self._evidence_fingerprint:
            raise IntegrationHumanGateError(
                "current reproducible Git evidence no longer matches the gated conflict"
            )
        return current

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
            "evidence_fingerprint": self._evidence_fingerprint,
            "policy_fingerprint": self._policy_fingerprint,
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
                    f"human decision metadata does not match current gate evidence: {key}"
                )

        try:
            decision = HumanGateDecision(metadata["decision"])
        except ValueError as exc:
            raise IntegrationHumanGateError(
                "human decision marker contains an unsupported decision"
            ) from exc
        if (
            decision is HumanGateDecision.AUTHORIZE_REPAIR
            and not self._policy_decision.human_repair_authorizable
        ):
            raise IntegrationHumanGateError(
                "recorded human authorization violates current hard repair boundaries"
            )

        actor = self._decode_json_text(metadata["actor_json"], label="decision actor")
        note = self._decode_json_text(metadata["note_json"], label="decision note")
        record = HumanIntegrationDecision(
            decision=decision,
            actor=actor,
            note=note,
            decision_ref=self._decision_ref,
            decision_commit=decision_commit,
            evidence_fingerprint=self._evidence_fingerprint,
            policy_fingerprint=self._policy_fingerprint,
            conflict_marker_commit=self._evidence.marker_commit,
        )
        self._validate_human_metadata(actor=record.actor, note=record.note)
        return record

    def _create_decision_ref_transactionally(self, decision_commit: str) -> None:
        attempt = self._terminal_attempt()
        task_ref = f"refs/heads/{attempt.task_branch}"
        transaction = "\n".join(
            [
                "start",
                f"verify {self._queue_snapshot.integration_ref} {self._evidence.integration_head}",
                f"verify {self._evidence.conflict_ref} {self._evidence.marker_commit}",
                f"verify {task_ref} {attempt.task_commit}",
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
        actor_json = json.dumps(actor, ensure_ascii=False)
        note_json = json.dumps(note, ensure_ascii=False)
        return (
            f"DevFlow human integration decision: {decision.value}\n\n"
            f"DevFlow-Human-Decision: {decision.value}\n"
            f"DevFlow-Decision-Actor-JSON: {actor_json}\n"
            f"DevFlow-Decision-Note-JSON: {note_json}\n"
            f"DevFlow-Evidence-Fingerprint: {self._evidence_fingerprint}\n"
            f"DevFlow-Policy-Fingerprint: {self._policy_fingerprint}\n"
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
            "DevFlow-Decision-Actor-JSON: ": "actor_json",
            "DevFlow-Decision-Note-JSON: ": "note_json",
            "DevFlow-Evidence-Fingerprint: ": "evidence_fingerprint",
            "DevFlow-Policy-Fingerprint: ": "policy_fingerprint",
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
        if _SHA256_PATTERN.fullmatch(metadata["evidence_fingerprint"]) is None:
            raise IntegrationHumanGateError("recorded evidence fingerprint is malformed")
        if _SHA256_PATTERN.fullmatch(metadata["policy_fingerprint"]) is None:
            raise IntegrationHumanGateError("recorded policy fingerprint is malformed")
        return metadata

    @staticmethod
    def _decode_json_text(value: str, *, label: str) -> str:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise IntegrationHumanGateError(f"{label} metadata is not valid JSON") from exc
        if not isinstance(decoded, str):
            raise IntegrationHumanGateError(f"{label} metadata must decode to a string")
        return decoded

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
        attempt = self._terminal_attempt()
        task_head = self._resolve_commit(
            f"refs/heads/{attempt.task_branch}",
            label="conflicted task branch",
        )
        if task_head != attempt.task_commit:
            raise IntegrationHumanGateError("conflicted task branch moved outside the human gate")

    def _terminal_attempt(self) -> MergeQueueAttempt:
        return self._queue_snapshot.attempts[-1]

    @staticmethod
    def _validate_human_metadata(*, actor: str, note: str) -> None:
        if not actor or len(actor) > 128:
            raise ValueError("actor must contain between 1 and 128 characters")
        if len(note) > 512:
            raise ValueError("note must contain at most 512 characters")
        if any(value in actor or value in note for value in ("\n", "\r")):
            raise ValueError("human decision metadata must be single-line")

    def _blob_is_safe_text(self, object_id: str) -> bool:
        size_result = self._git(["cat-file", "-s", object_id], check=False)
        if size_result.returncode != 0:
            return False
        try:
            size = int(size_result.stdout.strip())
        except ValueError:
            return False
        if size < 0 or size > _MAX_AUTO_REPAIR_BLOB_BYTES:
            return False

        blob = self._git_bytes(["cat-file", "blob", object_id], check=False)
        if blob.returncode != 0 or len(blob.stdout) != size:
            return False
        if b"\0" in blob.stdout:
            return False
        try:
            blob.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True

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

    def _git_bytes(
        self,
        arguments: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git", "-C", str(self._workspace.root), *arguments]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=False,
                timeout=self._git_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise IntegrationHumanGateError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise IntegrationHumanGateError(
                "git blob inspection exceeded the configured timeout"
            ) from exc
        if check and completed.returncode != 0:
            raise IntegrationHumanGateError(
                "git blob inspection failed: "
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
