from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from app.agents.developer import DeveloperAgent
from app.models.developer import DeveloperStopReason
from app.models.dispatch import WorkerExecutionEvidence, WorkerExecutionStatus
from app.models.integration_gate import (
    HumanGateDecision,
    IntegrationGateSnapshot,
    IntegrationGateState,
)
from app.models.integration_repair import IntegrationConflictRepairEvidence
from app.models.merge import MergeQueueSnapshot
from app.models.task import TaskContract
from app.persistence.errors import PersistenceConflictError, PersistenceCorruptionError
from app.persistence.types import PersistedRunSnapshot, PersistenceEvidenceKind
from app.verification import DeterministicVerifier
from app.workspace import LocalGitWorkspace

_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
_CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


class IntegrationRepairError(RuntimeError):
    """Raised when a human-authorized repair cannot advance without weakening evidence gates."""


class IntegrationRepairEvidenceStore(Protocol):
    async def load_run(self, run_id: UUID) -> PersistedRunSnapshot: ...


class IntegrationRepairWriter(Protocol):
    async def record_integration_repair(
        self,
        evidence: IntegrationConflictRepairEvidence,
    ) -> tuple[int, str]: ...


class IntegrationRepairWorkspaceResolver(Protocol):
    def resolve(self, project_id: UUID) -> LocalGitWorkspace: ...


class IntegrationConflictRepairService:
    """Resolve one explicit Human-Gate conflict under a conflict-path-only Agent scope.

    Human authorization is necessary but never sufficient. The service reuses the exact accepted
    Git conflict and grants the Developer Agent write access only to classified conflict paths.
    It then runs original TaskContract verification against the full merged tree, anchors the
    repair commit with a server-owned staging ref, persists typed repair evidence, and only then
    CAS-advances the integration ref. The staging ref protects the persisted repair object across
    a process crash without becoming execution authority.
    """

    def __init__(
        self,
        *,
        evidence_store: IntegrationRepairEvidenceStore,
        repair_store: IntegrationRepairWriter,
        workspace_resolver: IntegrationRepairWorkspaceResolver,
        developer: DeveloperAgent,
        verifier: DeterministicVerifier,
        repair_root: str | Path,
        git_timeout_seconds: float = 30.0,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        self._evidence_store = evidence_store
        self._repair_store = repair_store
        self._workspace_resolver = workspace_resolver
        self._developer = developer
        self._verifier = verifier
        self._repair_root = Path(repair_root).expanduser().resolve(strict=False)
        self._git_timeout_seconds = git_timeout_seconds

    async def repair(
        self,
        *,
        run_id: UUID,
        gate: IntegrationGateSnapshot,
        queue_snapshot: MergeQueueSnapshot,
    ) -> IntegrationConflictRepairEvidence:
        self._validate_gate_shape(gate, queue_snapshot)
        snapshot = await self._evidence_store.load_run(run_id)
        task = self._task_contract(snapshot, gate.task_id)
        worker = self._successful_worker(snapshot, gate.task_id)
        if worker.commit_sha != gate.task_commit:
            raise PersistenceCorruptionError(
                "Human Gate task commit disagrees with accepted worker execution evidence"
            )

        workspace = self._workspace_resolver.resolve(snapshot.project_id)
        self._assert_base_clean(workspace)
        staging_ref = self._repair_staging_ref(run_id, gate)
        existing = self._existing_repair(snapshot, gate)
        if existing is not None:
            self._validate_repair_commit(workspace, existing, worker.branch_name or "")
            self._anchor_repair_commit(workspace, staging_ref, existing.repair_commit)
            self._advance_ref(workspace, queue_snapshot.integration_ref, existing)
            self._archive_live_gate_refs(workspace, gate)
            self._release_repair_anchor(workspace, staging_ref, existing.repair_commit)
            return existing

        repair_task = self._conflict_only_task(task, gate)
        repair_path = self._repair_path(run_id, gate.task_id)
        self._add_repair_worktree(workspace, repair_path, gate.integration_head)
        try:
            repair_workspace = LocalGitWorkspace(
                repair_path,
                git_timeout_seconds=self._git_timeout_seconds,
            )
            merge = self._git(
                repair_workspace,
                ["merge", "--no-commit", "--no-ff", gate.task_commit],
                check=False,
            )
            if merge.returncode != 1:
                raise IntegrationRepairError(
                    "authorized repair did not reproduce the exact Git merge conflict"
                )
            unresolved = self._unmerged_paths(repair_workspace)
            if tuple(sorted(unresolved)) != tuple(sorted(gate.policy.conflicting_paths)):
                raise IntegrationRepairError(
                    "reproduced conflict paths differ from the Human Gate evidence"
                )

            developer_run = await self._developer.run(
                repair_task,
                workspace=repair_workspace,
            )
            if developer_run.stop_reason is not DeveloperStopReason.MODEL_STOP:
                raise IntegrationRepairError(
                    "integration repair Agent did not terminate within its bounded success path"
                )
            self._assert_no_text_conflict_markers(repair_workspace, gate.policy.conflicting_paths)
            self._git(
                repair_workspace,
                ["add", "-A", "--", *gate.policy.conflicting_paths],
            )
            remaining = self._unmerged_paths(repair_workspace)
            if remaining:
                raise IntegrationRepairError(
                    "integration repair left unresolved Git index stages: " + ", ".join(remaining)
                )

            verification = self._verifier.verify(task, workspace=repair_workspace)
            if not verification.passed:
                raise IntegrationRepairError(
                    "human-authorized integration repair failed deterministic verification"
                )
            changed_files = tuple(repair_workspace.changed_files())
            if not changed_files:
                raise IntegrationRepairError("integration repair produced no repository changes")

            tree = self._git(repair_workspace, ["write-tree"]).stdout.strip()
            self._require_oid(tree, label="repair tree")
            repair_commit = self._git(
                repair_workspace,
                [
                    "commit-tree",
                    tree,
                    "-p",
                    gate.integration_head,
                    "-p",
                    gate.task_commit,
                    "-m",
                    self._repair_message(
                        gate=gate,
                        task=task,
                        branch_name=worker.branch_name or "",
                        task_base=worker.base_commit,
                    ),
                ],
                env=self._commit_environment(),
            ).stdout.strip()
            self._require_oid(repair_commit, label="integration repair commit")
            self._anchor_repair_commit(workspace, staging_ref, repair_commit)

            evidence = IntegrationConflictRepairEvidence(
                run_id=run_id,
                task_id=gate.task_id,
                integration_head=gate.integration_head,
                task_commit=gate.task_commit,
                conflict_marker_commit=gate.conflict_marker_commit,
                conflict_evidence_fingerprint=gate.evidence_fingerprint,
                policy_fingerprint=gate.policy_fingerprint,
                human_decision_commit=gate.human_decision.decision_commit,
                conflicting_paths=gate.policy.conflicting_paths,
                repair_commit=repair_commit,
                changed_files=changed_files,
                developer_run=developer_run,
                verification=verification,
            )
            await self._repair_store.record_integration_repair(evidence)
            self._advance_ref(workspace, queue_snapshot.integration_ref, evidence)
            self._archive_live_gate_refs(workspace, gate)
            self._release_repair_anchor(workspace, staging_ref, evidence.repair_commit)
            return evidence
        finally:
            self._remove_repair_worktree(workspace, repair_path)

    @staticmethod
    def _validate_gate_shape(
        gate: IntegrationGateSnapshot,
        queue_snapshot: MergeQueueSnapshot,
    ) -> None:
        if gate.state is not IntegrationGateState.REPAIR_AUTHORIZED:
            raise IntegrationRepairError("integration repair requires REPAIR_AUTHORIZED Human Gate")
        if not gate.repair_may_start or gate.human_decision is None:
            raise IntegrationRepairError("Human Gate does not authorize a repair stage")
        if gate.human_decision.decision is not HumanGateDecision.AUTHORIZE_REPAIR:
            raise IntegrationRepairError("Human Gate decision is not AUTHORIZE_REPAIR")
        if not queue_snapshot.stopped or not queue_snapshot.attempts:
            raise IntegrationRepairError("integration repair requires a stopped merge queue")
        terminal = queue_snapshot.attempts[-1]
        if (
            terminal.task_id != gate.task_id
            or terminal.task_commit != gate.task_commit
            or terminal.previous_integration_commit != gate.integration_head
        ):
            raise IntegrationRepairError("Human Gate does not match the stopped merge queue")

    @staticmethod
    def _task_contract(snapshot: PersistedRunSnapshot, task_id: str) -> TaskContract:
        matches = [item.task for item in snapshot.tasks if item.task.task_id == task_id]
        if len(matches) != 1:
            raise PersistenceCorruptionError("repair task identity is not unique in persisted Run")
        return matches[0]

    @staticmethod
    def _successful_worker(
        snapshot: PersistedRunSnapshot,
        task_id: str,
    ) -> WorkerExecutionEvidence:
        matches: list[WorkerExecutionEvidence] = []
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.WORKER_EXECUTION or item.task_id != task_id:
                continue
            evidence = WorkerExecutionEvidence.model_validate(item.payload)
            if evidence.status is WorkerExecutionStatus.SUCCEEDED:
                matches.append(evidence)
        if len(matches) != 1 or matches[0].commit_sha is None or not matches[0].branch_name:
            raise PersistenceCorruptionError(
                "integration repair requires exactly one successful worker commit evidence"
            )
        return matches[0]

    @staticmethod
    def _existing_repair(
        snapshot: PersistedRunSnapshot,
        gate: IntegrationGateSnapshot,
    ) -> IntegrationConflictRepairEvidence | None:
        matches: list[IntegrationConflictRepairEvidence] = []
        for item in snapshot.evidence:
            if item.kind is not PersistenceEvidenceKind.INTEGRATION_REPAIR:
                continue
            repair = IntegrationConflictRepairEvidence.model_validate(item.payload)
            if (
                repair.task_id == gate.task_id
                and repair.conflict_evidence_fingerprint == gate.evidence_fingerprint
            ):
                matches.append(repair)
        if len(matches) > 1:
            raise PersistenceCorruptionError(
                "one Human Gate conflict contains multiple integration repair results"
            )
        return matches[0] if matches else None

    @staticmethod
    def _conflict_only_task(task: TaskContract, gate: IntegrationGateSnapshot) -> TaskContract:
        conflict_paths = list(gate.policy.conflicting_paths)
        return TaskContract(
            task_id=task.task_id,
            objective=(
                "Resolve only the already-classified Git integration conflicts for this task. "
                "Preserve the intended task behavior and both sides' compatible changes. Do not "
                "edit non-conflicting files. Original objective: " + task.objective
            ),
            readable_files=list(
                dict.fromkeys([*task.readable_files, *task.writable_files, *conflict_paths])
            ),
            writable_files=conflict_paths,
            readonly_files=task.readonly_files,
            acceptance_criteria=task.acceptance_criteria,
            verification_commands=task.verification_commands,
            max_retries=0,
        )

    @staticmethod
    def _repair_staging_ref(run_id: UUID, gate: IntegrationGateSnapshot) -> str:
        return f"refs/devflow/integration-repairs/{run_id.hex}/{gate.conflict_marker_commit}"

    def _repair_path(self, run_id: UUID, task_id: str) -> Path:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", task_id).strip("-_").lower() or "task"
        path = self._repair_root / run_id.hex / f"{slug[:48]}-{uuid4().hex[:12]}"
        self._repair_root.mkdir(parents=True, exist_ok=True)
        return path

    def _add_repair_worktree(
        self,
        workspace: LocalGitWorkspace,
        path: Path,
        integration_head: str,
    ) -> None:
        if path.exists():
            raise IntegrationRepairError("fresh integration repair path unexpectedly exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._git(
            workspace,
            ["worktree", "add", "--detach", str(path), integration_head],
            check=False,
        )
        if result.returncode != 0:
            raise IntegrationRepairError(
                "Git could not create isolated integration repair worktree"
            )

    def _remove_repair_worktree(self, workspace: LocalGitWorkspace, path: Path) -> None:
        if path.exists():
            self._git(workspace, ["worktree", "remove", "--force", str(path)], check=False)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def _anchor_repair_commit(
        self,
        workspace: LocalGitWorkspace,
        staging_ref: str,
        repair_commit: str,
    ) -> None:
        current = self._resolve_optional_ref(workspace, staging_ref)
        if current == repair_commit:
            return
        if current is not None:
            raise PersistenceConflictError(
                "integration repair staging ref already protects a different commit"
            )
        zero_oid = "0" * len(repair_commit)
        created = self._git(
            workspace,
            [
                "update-ref",
                "-m",
                "DevFlow durable integration repair staging",
                staging_ref,
                repair_commit,
                zero_oid,
            ],
            check=False,
        )
        if created.returncode != 0:
            raise PersistenceConflictError(
                "could not create durable integration repair staging ref"
            )
        if self._resolve_ref(workspace, staging_ref) != repair_commit:
            raise PersistenceCorruptionError(
                "integration repair staging ref did not protect the expected commit"
            )

    def _release_repair_anchor(
        self,
        workspace: LocalGitWorkspace,
        staging_ref: str,
        repair_commit: str,
    ) -> None:
        current = self._resolve_optional_ref(workspace, staging_ref)
        if current is None:
            return
        if current != repair_commit:
            raise PersistenceConflictError("integration repair staging ref changed before cleanup")
        deleted = self._git(
            workspace,
            ["update-ref", "-d", staging_ref, repair_commit],
            check=False,
        )
        if deleted.returncode != 0:
            raise PersistenceConflictError(
                "could not release durable integration repair staging ref"
            )
        if self._resolve_optional_ref(workspace, staging_ref) is not None:
            raise PersistenceCorruptionError(
                "integration repair staging ref remained after cleanup"
            )

    def _advance_ref(
        self,
        workspace: LocalGitWorkspace,
        integration_ref: str,
        evidence: IntegrationConflictRepairEvidence,
    ) -> None:
        current = self._resolve_ref(workspace, integration_ref)
        if current == evidence.repair_commit:
            return
        if current != evidence.integration_head:
            raise PersistenceConflictError(
                "integration ref moved before the persisted repair could be applied"
            )
        update = self._git(
            workspace,
            [
                "update-ref",
                "-m",
                f"DevFlow repair {evidence.task_id}",
                integration_ref,
                evidence.repair_commit,
                evidence.integration_head,
            ],
            check=False,
        )
        if update.returncode != 0:
            raise PersistenceConflictError("integration repair CAS update lost a concurrent race")
        if self._resolve_ref(workspace, integration_ref) != evidence.repair_commit:
            raise PersistenceCorruptionError(
                "integration ref did not land on persisted repair commit"
            )

    def _archive_live_gate_refs(
        self,
        workspace: LocalGitWorkspace,
        gate: IntegrationGateSnapshot,
    ) -> None:
        integration_id = gate.integration_ref.removeprefix("refs/devflow/integration/")
        if integration_id == gate.integration_ref or not integration_id:
            raise PersistenceCorruptionError("Human Gate integration ref has unexpected namespace")
        decision = gate.human_decision
        if decision is None:
            raise PersistenceCorruptionError("repair gate lost its human decision")
        items = (
            (
                gate.conflict_ref,
                gate.conflict_marker_commit,
                f"refs/devflow/integration-conflict-history/{integration_id}/"
                f"{gate.conflict_marker_commit}",
            ),
            (
                decision.decision_ref,
                decision.decision_commit,
                f"refs/devflow/integration-decision-history/{integration_id}/"
                f"{gate.conflict_marker_commit}",
            ),
        )
        for live_ref, expected_oid, archive_ref in items:
            live = self._resolve_optional_ref(workspace, live_ref)
            archive = self._resolve_optional_ref(workspace, archive_ref)
            if archive is not None and archive != expected_oid:
                raise PersistenceCorruptionError(
                    "integration repair archive ref changed unexpectedly"
                )
            if archive is None:
                zero_oid = "0" * len(expected_oid)
                created = self._git(
                    workspace,
                    ["update-ref", archive_ref, expected_oid, zero_oid],
                    check=False,
                )
                if created.returncode != 0:
                    raise PersistenceConflictError("could not archive durable Human Gate Git ref")
            if live is None:
                continue
            if live != expected_oid:
                raise PersistenceConflictError("live Human Gate Git ref changed before archival")
            deleted = self._git(
                workspace,
                ["update-ref", "-d", live_ref, expected_oid],
                check=False,
            )
            if deleted.returncode != 0:
                raise PersistenceConflictError("could not release live Human Gate Git ref")

    def _validate_repair_commit(
        self,
        workspace: LocalGitWorkspace,
        evidence: IntegrationConflictRepairEvidence,
        branch_name: str,
    ) -> None:
        parents = (
            self._git(
                workspace,
                ["show", "-s", "--format=%P", evidence.repair_commit],
            )
            .stdout.strip()
            .split()
        )
        if tuple(parents) != (evidence.integration_head, evidence.task_commit):
            raise PersistenceCorruptionError("persisted integration repair commit parents changed")
        message = self._git(
            workspace,
            ["show", "-s", "--format=%B", evidence.repair_commit],
        ).stdout
        expected_lines = {
            f"DevFlow-Task: {evidence.task_id}",
            f"DevFlow-Task-Branch: {branch_name}",
            f"DevFlow-Task-Commit: {evidence.task_commit}",
            "DevFlow-Integration-Repair: true",
            f"DevFlow-Conflict-Marker: {evidence.conflict_marker_commit}",
            f"DevFlow-Conflict-Evidence-Fingerprint: {evidence.conflict_evidence_fingerprint}",
            f"DevFlow-Policy-Fingerprint: {evidence.policy_fingerprint}",
            f"DevFlow-Human-Decision-Commit: {evidence.human_decision_commit}",
        }
        lines = set(message.splitlines())
        if not expected_lines.issubset(lines):
            raise PersistenceCorruptionError("persisted integration repair commit metadata changed")

    @staticmethod
    def _repair_message(
        *,
        gate: IntegrationGateSnapshot,
        task: TaskContract,
        branch_name: str,
        task_base: str,
    ) -> str:
        decision = gate.human_decision
        if decision is None:
            raise IntegrationRepairError("repair message requires Human Gate decision")
        return (
            f"DevFlow repair integration conflict for {task.task_id}\n\n"
            f"DevFlow-Task: {task.task_id}\n"
            f"DevFlow-Task-Branch: {branch_name}\n"
            f"DevFlow-Task-Base: {task_base}\n"
            f"DevFlow-Task-Commit: {gate.task_commit}\n"
            "DevFlow-Integration-Repair: true\n"
            f"DevFlow-Conflict-Marker: {gate.conflict_marker_commit}\n"
            f"DevFlow-Conflict-Evidence-Fingerprint: {gate.evidence_fingerprint}\n"
            f"DevFlow-Policy-Fingerprint: {gate.policy_fingerprint}\n"
            f"DevFlow-Human-Decision-Commit: {decision.decision_commit}"
        )

    @staticmethod
    def _assert_no_text_conflict_markers(
        workspace: LocalGitWorkspace,
        paths: tuple[str, ...],
    ) -> None:
        for repository_path in paths:
            path = workspace.resolve_path(repository_path)
            if not path.exists():
                continue
            if not path.is_file():
                raise IntegrationRepairError(
                    f"conflict path is not a regular file after repair: {repository_path}"
                )
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise IntegrationRepairError(
                    f"conflict path is not UTF-8 text after repair: {repository_path}"
                ) from exc
            if any(marker in text for marker in _CONFLICT_MARKERS):
                raise IntegrationRepairError(
                    f"conflict marker remains after Agent repair: {repository_path}"
                )

    def _unmerged_paths(self, workspace: LocalGitWorkspace) -> tuple[str, ...]:
        value = self._git(
            workspace,
            ["diff", "--name-only", "--diff-filter=U", "-z", "--"],
        ).stdout
        return tuple(sorted(item for item in value.split("\0") if item))

    @staticmethod
    def _assert_base_clean(workspace: LocalGitWorkspace) -> None:
        if workspace.changed_files():
            raise IntegrationRepairError(
                "base workspace must remain clean before integration repair"
            )

    def _resolve_ref(self, workspace: LocalGitWorkspace, ref: str) -> str:
        value = self._git(
            workspace,
            ["rev-parse", "--verify", f"{ref}^{{commit}}"],
        ).stdout.strip()
        self._require_oid(value, label="Git ref")
        return value

    def _resolve_optional_ref(self, workspace: LocalGitWorkspace, ref: str) -> str | None:
        result = self._git(
            workspace,
            ["rev-parse", "--verify", f"{ref}^{{commit}}"],
            check=False,
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        self._require_oid(value, label="optional Git ref")
        return value

    def _git(
        self,
        workspace: LocalGitWorkspace,
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(workspace.root), *arguments],
                capture_output=True,
                text=True,
                timeout=self._git_timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            raise IntegrationRepairError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise IntegrationRepairError("integration repair Git command timed out") from exc
        if check and completed.returncode != 0:
            raise IntegrationRepairError(
                "integration repair Git command failed: "
                + (completed.stderr.strip() or f"exit_code={completed.returncode}")
            )
        return completed

    @staticmethod
    def _commit_environment() -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_AUTHOR_NAME": "DevFlow",
                "GIT_AUTHOR_EMAIL": "devflow@local.invalid",
                "GIT_COMMITTER_NAME": "DevFlow",
                "GIT_COMMITTER_EMAIL": "devflow@local.invalid",
            }
        )
        return env

    @staticmethod
    def _require_oid(value: str, *, label: str) -> None:
        if _OID_RE.fullmatch(value) is None:
            raise PersistenceCorruptionError(f"{label} is not a full lowercase Git object id")
