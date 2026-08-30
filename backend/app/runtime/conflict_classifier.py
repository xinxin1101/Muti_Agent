from __future__ import annotations

import os
import re
import subprocess
from collections import OrderedDict

from app.models.conflict import (
    MergeConflictEvidence,
    MergeConflictFile,
    MergeConflictMessage,
    MergeConflictStage,
    MergeConflictStageSide,
)
from app.models.merge import MergeAttemptOutcome, MergeQueueSnapshot
from app.workspace import LocalGitWorkspace

_OID_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_INTEGRATION_PREFIX = "refs/devflow/integration/"
_CONFLICT_PREFIX = "refs/devflow/integration-conflicts/"
_DEFAULT_RAW_EVIDENCE_LIMIT = 8_000
_STAGE_SIDES = {
    1: MergeConflictStageSide.BASE,
    2: MergeConflictStageSide.INTEGRATION,
    3: MergeConflictStageSide.TASK,
}


class MergeConflictClassificationError(RuntimeError):
    """Raised when conflict evidence cannot be reproduced or parsed safely."""


class GitMergeConflictClassifier:
    """Classify a stopped merge queue from reproducible object-level Git evidence."""

    def __init__(
        self,
        workspace: LocalGitWorkspace,
        *,
        git_timeout_seconds: float = 15.0,
        raw_evidence_limit: int = _DEFAULT_RAW_EVIDENCE_LIMIT,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if raw_evidence_limit < 256 or raw_evidence_limit > 16_000:
            raise ValueError("raw_evidence_limit must be between 256 and 16000 characters")
        self._workspace = workspace
        self._git_timeout_seconds = git_timeout_seconds
        self._raw_evidence_limit = raw_evidence_limit

    def classify(self, snapshot: MergeQueueSnapshot) -> MergeConflictEvidence:
        """Return structured conflict evidence without modifying the index or working tree."""

        if self._workspace.changed_files():
            raise MergeConflictClassificationError(
                "base workspace must be clean before conflict classification"
            )
        if not snapshot.stopped or not snapshot.attempts:
            raise MergeConflictClassificationError(
                "merge queue snapshot does not contain a terminal conflict"
            )

        attempt = snapshot.attempts[-1]
        if attempt.outcome is not MergeAttemptOutcome.CONFLICT:
            raise MergeConflictClassificationError("merge queue terminal attempt is not a conflict")
        if snapshot.head_commit != attempt.previous_integration_commit:
            raise MergeConflictClassificationError(
                "conflict attempt does not reference the current integration head"
            )

        conflict_ref = self._derive_conflict_ref(snapshot.integration_ref)
        current_head = self._resolve_commit(snapshot.integration_ref, label="integration ref")
        if current_head != snapshot.head_commit:
            raise MergeConflictClassificationError(
                "integration ref moved after the queue snapshot was captured"
            )

        marker_commit = self._resolve_commit(conflict_ref, label="conflict marker ref")
        if self._commit_parents(marker_commit) != (snapshot.head_commit,):
            raise MergeConflictClassificationError(
                "conflict marker does not have the integration head as its sole parent"
            )
        if self._resolve_tree(marker_commit) != self._resolve_tree(snapshot.head_commit):
            raise MergeConflictClassificationError(
                "conflict marker unexpectedly changes the integration tree"
            )

        metadata = self._parse_marker_metadata(marker_commit)
        expected_metadata = {
            "task": attempt.task_id,
            "branch": attempt.task_branch,
            "base": attempt.task_base_commit,
            "commit": attempt.task_commit,
            "integration_head": snapshot.head_commit,
        }
        if metadata != expected_metadata:
            raise MergeConflictClassificationError(
                "conflict marker metadata does not match the terminal merge attempt"
            )

        branch_head = self._resolve_commit(
            f"refs/heads/{attempt.task_branch}",
            label="conflicted task branch",
        )
        if branch_head != attempt.task_commit:
            raise MergeConflictClassificationError(
                "conflicted task branch moved after worker finalization"
            )
        if self._commit_parents(attempt.task_commit) != (attempt.task_base_commit,):
            raise MergeConflictClassificationError(
                "conflicted task commit no longer matches its recorded task base"
            )

        merge = self._git(
            [
                "merge-tree",
                "--write-tree",
                "-z",
                "--messages",
                snapshot.head_commit,
                attempt.task_commit,
            ],
            check=False,
        )
        if merge.returncode != 1:
            raise MergeConflictClassificationError(
                "recorded conflict cannot be reproduced by git merge-tree"
            )

        evidence = self._parse_conflicted_merge_output(
            stdout=merge.stdout,
            stderr=merge.stderr,
            integration_head=snapshot.head_commit,
            task_commit=attempt.task_commit,
            conflict_ref=conflict_ref,
            marker_commit=marker_commit,
        )
        if self._workspace.changed_files():
            raise MergeConflictClassificationError(
                "conflict classification unexpectedly modified the base workspace"
            )
        return evidence

    def _parse_conflicted_merge_output(
        self,
        *,
        stdout: bytes,
        stderr: bytes,
        integration_head: str,
        task_commit: str,
        conflict_ref: str,
        marker_commit: str,
    ) -> MergeConflictEvidence:
        parts = stdout.split(b"\0")
        if len(parts) < 3 or parts[-1] != b"":
            raise MergeConflictClassificationError("merge-tree -z output is not NUL terminated")

        conflicted_tree = self._decode_oid(parts[0], label="conflicted tree")
        index = 1
        grouped: OrderedDict[str, list[MergeConflictStage]] = OrderedDict()

        while index < len(parts) and parts[index] != b"":
            path, stage = self._parse_stage_record(parts[index])
            existing = grouped.setdefault(path, [])
            if any(item.stage == stage.stage for item in existing):
                raise MergeConflictClassificationError(
                    f"merge-tree returned duplicate stage {stage.stage} for {path}"
                )
            existing.append(stage)
            index += 1

        if index >= len(parts):
            raise MergeConflictClassificationError(
                "merge-tree output is missing the conflict-message separator"
            )
        index += 1

        messages: list[MergeConflictMessage] = []
        while index < len(parts) - 1:
            if parts[index] == b"":
                if any(parts[index:]):
                    raise MergeConflictClassificationError(
                        "merge-tree output contains unexpected empty message records"
                    )
                break
            path_count = self._decode_count(parts[index])
            index += 1
            if index + path_count + 2 > len(parts):
                raise MergeConflictClassificationError("merge-tree message record is truncated")
            paths = tuple(os.fsdecode(value) for value in parts[index : index + path_count])
            index += path_count
            conflict_type = self._decode_text(parts[index]).strip()
            index += 1
            message = self._decode_text(parts[index]).strip()
            index += 1
            if not conflict_type or not message:
                raise MergeConflictClassificationError(
                    "merge-tree message record is missing type or message"
                )
            messages.append(
                MergeConflictMessage(
                    conflict_type=conflict_type,
                    paths=paths,
                    message=message,
                )
            )

        if not grouped:
            raise MergeConflictClassificationError(
                "merge-tree reported a conflict without staged conflict paths"
            )

        files = tuple(
            MergeConflictFile(
                path=path,
                stages=tuple(sorted(stages, key=lambda value: value.stage)),
            )
            for path, stages in grouped.items()
        )
        conflict_types = self._conflict_types(messages)
        raw_git_evidence, truncated = self._bounded_raw_evidence(stdout, stderr)

        return MergeConflictEvidence(
            integration_head=integration_head,
            task_commit=task_commit,
            conflict_ref=conflict_ref,
            marker_commit=marker_commit,
            conflicted_tree=conflicted_tree,
            conflicting_paths=tuple(file.path for file in files),
            conflict_types=conflict_types,
            files=files,
            messages=tuple(messages),
            raw_git_evidence=raw_git_evidence,
            raw_git_evidence_truncated=truncated,
        )

    @staticmethod
    def _parse_stage_record(record: bytes) -> tuple[str, MergeConflictStage]:
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator or not raw_path:
            raise MergeConflictClassificationError(
                "merge-tree conflict stage record has an invalid path boundary"
            )
        fields = metadata.split()
        if len(fields) != 3:
            raise MergeConflictClassificationError(
                "merge-tree conflict stage record must contain mode, object, and stage"
            )
        try:
            mode = fields[0].decode("ascii")
            object_id = fields[1].decode("ascii")
            stage_number = int(fields[2].decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MergeConflictClassificationError(
                "merge-tree conflict stage metadata is malformed"
            ) from exc
        if stage_number not in _STAGE_SIDES:
            raise MergeConflictClassificationError(
                f"merge-tree returned unsupported conflict stage: {stage_number}"
            )
        if re.fullmatch(r"[0-7]{6}", mode) is None:
            raise MergeConflictClassificationError("merge-tree returned an invalid file mode")
        if _OID_PATTERN.fullmatch(object_id) is None:
            raise MergeConflictClassificationError("merge-tree returned an invalid stage object id")

        path = os.fsdecode(raw_path)
        if not path:
            raise MergeConflictClassificationError("merge-tree returned an empty conflict path")
        return (
            path,
            MergeConflictStage(
                stage=stage_number,
                side=_STAGE_SIDES[stage_number],
                mode=mode,
                object_id=object_id,
            ),
        )

    @staticmethod
    def _conflict_types(messages: list[MergeConflictMessage]) -> tuple[str, ...]:
        ordered_types: list[str] = []
        for message in messages:
            if not message.conflict_type.startswith("CONFLICT"):
                continue
            if message.conflict_type not in ordered_types:
                ordered_types.append(message.conflict_type)
        if ordered_types:
            return tuple(ordered_types)

        for message in messages:
            if message.conflict_type == "Auto-merging":
                continue
            if message.conflict_type not in ordered_types:
                ordered_types.append(message.conflict_type)
        return tuple(ordered_types)

    def _bounded_raw_evidence(self, stdout: bytes, stderr: bytes) -> tuple[str, bool]:
        stdout_text = self._raw_text(stdout)
        stderr_text = self._raw_text(stderr)
        raw = f"stdout={stdout_text}"
        if stderr_text:
            raw += f"\nstderr={stderr_text}"
        if len(raw) <= self._raw_evidence_limit:
            return raw, False
        suffix = "\n...[truncated]"
        return raw[: self._raw_evidence_limit - len(suffix)] + suffix, True

    @staticmethod
    def _raw_text(value: bytes) -> str:
        return value.decode("utf-8", errors="backslashreplace").replace("\x00", "\\0")

    @staticmethod
    def _decode_text(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")

    @staticmethod
    def _decode_count(value: bytes) -> int:
        try:
            count = int(value.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MergeConflictClassificationError(
                "merge-tree message path count is malformed"
            ) from exc
        if count < 0 or count > 1_000:
            raise MergeConflictClassificationError(
                "merge-tree message path count is outside the supported bound"
            )
        return count

    @staticmethod
    def _derive_conflict_ref(integration_ref: str) -> str:
        if not integration_ref.startswith(_INTEGRATION_PREFIX):
            raise MergeConflictClassificationError(
                "snapshot integration ref is outside the DevFlow integration namespace"
            )
        suffix = integration_ref[len(_INTEGRATION_PREFIX) :]
        if not suffix:
            raise MergeConflictClassificationError("snapshot integration ref has no run identity")
        return _CONFLICT_PREFIX + suffix

    def _parse_marker_metadata(self, marker_commit: str) -> dict[str, str]:
        message = self._git(["show", "-s", "--format=%B", marker_commit]).stdout
        text = self._decode_text(message)
        prefixes = {
            "DevFlow-Conflict-Task: ": "task",
            "DevFlow-Conflict-Branch: ": "branch",
            "DevFlow-Conflict-Base: ": "base",
            "DevFlow-Conflict-Commit: ": "commit",
            "DevFlow-Conflict-Integration-Head: ": "integration_head",
        }
        metadata: dict[str, str] = {}
        for line in text.splitlines():
            for prefix, key in prefixes.items():
                if not line.startswith(prefix):
                    continue
                if key in metadata:
                    raise MergeConflictClassificationError(
                        "conflict marker contains duplicate DevFlow metadata"
                    )
                metadata[key] = line[len(prefix) :].strip()
        if set(metadata) != set(prefixes.values()):
            raise MergeConflictClassificationError(
                "conflict marker is missing required DevFlow metadata"
            )
        self._require_oid(metadata["base"], label="conflict marker task base")
        self._require_oid(metadata["commit"], label="conflict marker task commit")
        self._require_oid(
            metadata["integration_head"],
            label="conflict marker integration head",
        )
        return metadata

    def _resolve_commit(self, ref: str, *, label: str) -> str:
        result = self._git(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        resolved = self._decode_text(result.stdout).strip()
        if result.returncode != 0 or _OID_PATTERN.fullmatch(resolved) is None:
            raise MergeConflictClassificationError(f"{label} does not resolve to a full commit id")
        return resolved

    def _resolve_tree(self, ref: str) -> str:
        result = self._git(["rev-parse", "--verify", f"{ref}^{{tree}}"], check=False)
        resolved = self._decode_text(result.stdout).strip()
        if result.returncode != 0 or _OID_PATTERN.fullmatch(resolved) is None:
            raise MergeConflictClassificationError(
                "Git reference does not resolve to a full tree id"
            )
        return resolved

    def _commit_parents(self, commit: str) -> tuple[str, ...]:
        result = self._git(["rev-list", "--parents", "-n", "1", commit])
        values = self._decode_text(result.stdout).strip().split()
        if not values or values[0] != commit:
            raise MergeConflictClassificationError(
                "Git returned inconsistent commit-parent evidence"
            )
        return tuple(values[1:])

    @staticmethod
    def _decode_oid(value: bytes, *, label: str) -> str:
        try:
            decoded = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise MergeConflictClassificationError(f"{label} is not ASCII") from exc
        if _OID_PATTERN.fullmatch(decoded) is None:
            raise MergeConflictClassificationError(f"{label} is not a full Git object id")
        return decoded

    @staticmethod
    def _require_oid(value: str, *, label: str) -> None:
        if _OID_PATTERN.fullmatch(value) is None:
            raise MergeConflictClassificationError(f"{label} is not a full Git object id")

    def _git(
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
            raise MergeConflictClassificationError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise MergeConflictClassificationError(
                "git conflict-classification command exceeded the configured timeout"
            ) from exc

        if check and completed.returncode != 0:
            raise MergeConflictClassificationError(
                "git conflict-classification command failed: "
                f"exit_code={completed.returncode}, operation={arguments[0]}"
            )
        return completed
