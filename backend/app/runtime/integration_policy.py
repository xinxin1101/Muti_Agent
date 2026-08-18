from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import PurePosixPath

from app.models.conflict import MergeConflictEvidence, MergeConflictStageShape
from app.models.integration_gate import IntegrationPolicyDecision, IntegrationPolicyRoute
from app.models.task import TaskContract
from app.workspace.scope import ScopeEnforcer

_DEFAULT_AUTO_REPAIR_SUFFIXES = frozenset({".md", ".py", ".pyi", ".rst", ".txt"})
_DEFAULT_PROTECTED_PREFIXES = (
    ".devflow/",
    ".github/",
    ".gitlab/",
    "backend/tests/",
    "tests/",
)
_DEFAULT_PROTECTED_BASENAMES = frozenset(
    {
        ".env",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
        "uv.lock",
        "yarn.lock",
    }
)


def conflict_evidence_fingerprint(evidence: MergeConflictEvidence) -> str:
    """Hash only structured Git facts used by policy, excluding free-form messages/raw output."""

    payload = {
        "integration_head": evidence.integration_head,
        "task_commit": evidence.task_commit,
        "conflict_ref": evidence.conflict_ref,
        "marker_commit": evidence.marker_commit,
        "conflicted_tree": evidence.conflicted_tree,
        "conflict_types": list(evidence.conflict_types),
        "files": [
            {
                "path": file.path,
                "stage_shape": file.stage_shape.value,
                "stages": [
                    {
                        "stage": stage.stage,
                        "side": stage.side.value,
                        "mode": stage.mode,
                        "object_id": stage.object_id,
                    }
                    for stage in file.stages
                ],
            }
            for file in evidence.files
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class IntegrationConflictPolicy:
    """Conservative deterministic router for merge conflicts.

    AUTO_REPAIR_CANDIDATE is only an eligibility signal for a later repair stage. It never
    means that a conflict is resolved and never authorizes integration-ref advancement.
    """

    def __init__(
        self,
        *,
        automatic_repair_enabled: bool = False,
        auto_repair_suffixes: frozenset[str] = _DEFAULT_AUTO_REPAIR_SUFFIXES,
        protected_prefixes: tuple[str, ...] = _DEFAULT_PROTECTED_PREFIXES,
        protected_basenames: frozenset[str] = _DEFAULT_PROTECTED_BASENAMES,
    ) -> None:
        if any(not suffix.startswith(".") or "/" in suffix for suffix in auto_repair_suffixes):
            raise ValueError("auto-repair suffixes must be simple dotted file suffixes")
        if any(not prefix or prefix.startswith("/") for prefix in protected_prefixes):
            raise ValueError("protected prefixes must be non-empty repository-relative prefixes")
        if any(not name or "/" in name for name in protected_basenames):
            raise ValueError("protected basenames must be simple file names")
        self._automatic_repair_enabled = automatic_repair_enabled
        self._auto_repair_suffixes = auto_repair_suffixes
        self._protected_prefixes = protected_prefixes
        self._protected_basenames = protected_basenames
        self._scope = ScopeEnforcer()

    @property
    def automatic_repair_enabled(self) -> bool:
        return self._automatic_repair_enabled

    def evaluate(
        self,
        evidence: MergeConflictEvidence,
        task: TaskContract,
        *,
        blob_is_safe_text: Callable[[str], bool] | None = None,
    ) -> IntegrationPolicyDecision:
        fingerprint = conflict_evidence_fingerprint(evidence)
        blockers: list[str] = []

        if not self._automatic_repair_enabled:
            blockers.append("automatic merge-conflict repair policy is disabled")

        scope = self._scope.check(task, list(evidence.conflicting_paths))
        if not scope.passed:
            blocked_paths = ", ".join(
                f"{violation.kind.value}:{violation.path}" for violation in scope.violations
            )
            blockers.append(f"conflict path is outside the task repair scope: {blocked_paths}")

        if len(evidence.files) != 1:
            blockers.append("automatic repair is limited to exactly one conflicted path")
        else:
            conflict_file = evidence.files[0]
            if conflict_file.stage_shape is not MergeConflictStageShape.THREE_WAY:
                blockers.append(
                    "automatic repair requires a THREE_WAY stage shape; "
                    f"observed {conflict_file.stage_shape.value}"
                )
            if any(stage.mode != "100644" for stage in conflict_file.stages):
                blockers.append("automatic repair requires regular non-executable file stages")
            if not self._is_supported_text_path(conflict_file.path):
                blockers.append("conflicted path is not in the narrow text-file allowlist")
            if self._is_protected_path(conflict_file.path):
                blockers.append("conflicted path is protected from automatic integration repair")
            if self._automatic_repair_enabled:
                if blob_is_safe_text is None:
                    blockers.append("automatic repair requires bounded Git blob text inspection")
                elif any(not blob_is_safe_text(stage.object_id) for stage in conflict_file.stages):
                    blockers.append(
                        "one or more conflicted Git blobs are not safe bounded UTF-8 text"
                    )

        if evidence.conflict_types != ("CONFLICT (contents)",):
            observed = ", ".join(evidence.conflict_types) or "<none>"
            blockers.append(
                "automatic repair requires only native CONFLICT (contents); "
                f"observed {observed}"
            )

        if blockers:
            return IntegrationPolicyDecision(
                route=IntegrationPolicyRoute.HUMAN_REQUIRED,
                evidence_fingerprint=fingerprint,
                automatic_repair_enabled=self._automatic_repair_enabled,
                conflicting_paths=evidence.conflicting_paths,
                reasons=tuple(blockers),
            )

        return IntegrationPolicyDecision(
            route=IntegrationPolicyRoute.AUTO_REPAIR_CANDIDATE,
            evidence_fingerprint=fingerprint,
            automatic_repair_enabled=True,
            conflicting_paths=evidence.conflicting_paths,
            reasons=(
                "policy permits only a later bounded repair attempt; integration remains stopped",
            ),
        )

    def _is_supported_text_path(self, path: str) -> bool:
        return PurePosixPath(path).suffix.lower() in self._auto_repair_suffixes

    def _is_protected_path(self, path: str) -> bool:
        normalized = path[2:] if path.startswith("./") else path
        if any(normalized.startswith(prefix) for prefix in self._protected_prefixes):
            return True
        return PurePosixPath(normalized).name in self._protected_basenames
