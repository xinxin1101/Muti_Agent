from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.merge import (
    MergeAttemptOutcome,
    MergeQueueAttempt,
    MergeQueueSnapshot,
)
from app.models.scheduler import TaskScheduleState
from app.models.worker import WorkerTaskResult
from app.runtime.scheduler import DAGScheduler
from app.workspace import LocalGitWorkspace, TaskWorktreeManager

_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40,64}$")
_INTEGRATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TERMINAL_NON_SUCCESS = {
    TaskScheduleState.FAILED,
    TaskScheduleState.BLOCKED,
}
_CONFLICT_EVIDENCE_LIMIT = 8_000


class MergeQueueError(RuntimeError):
    """Raised when integration cannot proceed without violating a trust boundary."""


class TopologicalMergeQueue:
    """Integrate successful task commits in deterministic DAG order without touching a worktree."""

    def __init__(
        self,
        *,
        scheduler: DAGScheduler,
        worktrees: TaskWorktreeManager,
        base_workspace: LocalGitWorkspace,
        integration_id: str,
        git_timeout_seconds: float = 15.0,
    ) -> None:
        if git_timeout_seconds <= 0:
            raise ValueError("git_timeout_seconds must be greater than zero")
        if _INTEGRATION_ID_PATTERN.fullmatch(integration_id) is None:
            raise ValueError(
                "integration_id must start with an alphanumeric character and contain only "
                "letters, digits, '.', '_', or '-'"
            )
        if base_workspace.changed_files():
            raise MergeQueueError("base workspace must be clean before integration starts")

        self._scheduler = scheduler
        self._worktrees = worktrees
        self._base_workspace = base_workspace
        self._git_timeout_seconds = git_timeout_seconds
        self._run_base = worktrees.base_commit
        self._integration_ref = f"refs/devflow/integration/{integration_id}"
        self._order = tuple(scheduler.dag.topological_order())
        self._order_index = {task_id: index for index, task_id in enumerate(self._order)}
        self._attempts: list[MergeQueueAttempt] = []
        self._integrated: list[str] = []
        self._stopped = False

        self._assert_commit_exists(self._run_base, label="frozen run base")
        self._validate_ref_name(self._integration_ref)
        self._head = self._initialize_or_recover_ref()

    @property
    def integration_ref(self) -> str:
        return self._integration_ref

    @property
    def head_commit(self) -> str:
        return self._head

    @property
    def integrated_task_ids(self) -> tuple[str, ...]:
        return tuple(self._integrated)

    def base_commit_for(self, task_id: str) -> str | None:
        """Return the trusted integration head for a dependency-ready downstream task."""

        node = self._scheduler.dag.node(task_id)
        if not node.depends_on:
            return None
        if self._scheduler.state(task_id) is not TaskScheduleState.READY:
            return None
        if not set(node.depends_on).issubset(self._integrated):
            return None
        self._assert_ref_head()
        return self._head

    def integrate(self, results: Sequence[WorkerTaskResult]) -> MergeQueueSnapshot:
        """Validate and integrate successful worker evidence in global topological order."""

        if self._stopped:
            raise MergeQueueError("merge queue is stopped after an unresolved integration conflict")
        if self._base_workspace.changed_files():
            raise MergeQueueError("base workspace became dirty before integration")
        self._assert_ref_head()

        candidates = tuple(results)
        if not candidates:
            return self.snapshot()
        task_ids = [result.task_id for result in candidates]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("worker integration results must not contain duplicate task ids")

        try:
            ordered = tuple(sorted(candidates, key=lambda item: self._order_index[item.task_id]))
        except KeyError as exc:
            raise ValueError(f"worker result references unknown DAG task: {exc.args[0]}") from exc

        simulated_integrated = set(self._integrated)
        for result in ordered:
            self._validate_worker_result(result)
            if result.task_id in simulated_integrated:
                raise MergeQueueError(f"task is already integrated: {result.task_id}")
            node = self._scheduler.dag.node(result.task_id)
            missing_dependencies = set(node.depends_on) - simulated_integrated
            if missing_dependencies:
                raise MergeQueueError(
                    f"task {result.task_id} cannot integrate before dependencies: "
                    + ", ".join(sorted(missing_dependencies))
                )
            self._assert_global_topological_gate(result.task_id, simulated_integrated)
            simulated_integrated.add(result.task_id)

        for result in ordered:
            if not self._integrate_one(result):
                break
        return self.snapshot()

    def snapshot(self) -> MergeQueueSnapshot:
        return MergeQueueSnapshot(
            integration_ref=self._integration_ref,
            run_base_commit=self._run_base,
            head_commit=self._head,
            integrated_task_ids=tuple(self._integrated),
            attempts=tuple(self._attempts),
            stopped=self._stopped,
        )

    def _integrate_one(self, result: WorkerTaskResult) -> bool:
        self._assert_ref_head()
        previous_head = self._head
        merge = self._git(
            ["merge-tree", "--write-tree", previous_head, result.commit_sha or ""],
            check=False,
        )
        if merge.returncode == 1:
            failure = self._merge_conflict_failure(
                task_id=result.task_id,
                integration_head=previous_head,
                task_commit=result.commit_sha or "",
                stdout=merge.stdout,
                stderr=merge.stderr,
            )
            self._attempts.append(
                MergeQueueAttempt(
                    sequence=len(self._attempts),
                    task_id=result.task_id,
                    task_branch=result.branch_name or "",
                    task_base_commit=result.base_commit or "",
                    task_commit=result.commit_sha or "",
                    previous_integration_commit=previous_head,
                    outcome=MergeAttemptOutcome.CONFLICT,
                    failure=failure,
                )
            )
            self._stopped = True
            self._assert_ref_head()
            self._assert_base_clean()
            return False
        if merge.returncode != 0:
            raise MergeQueueError(
                "git merge-tree failed before producing a trustworthy integration result: "
                f"exit_code={merge.returncode}"
            )

        tree = self._first_oid_line(merge.stdout, label="merged tree")
        commit_message = self._integration_message(result)
        commit = self._git(
            [
                "commit-tree",
                tree,
                "-p",
                previous_head,
                "-p",
                result.commit_sha or "",
                "-m",
                commit_message,
            ],
            env=self._commit_environment(),
        ).stdout.strip()
        self._require_full_oid(commit, label="integration commit")

        update = self._git(
            [
                "update-ref",
                "-m",
                f"DevFlow integrate {result.task_id}",
                self._integration_ref,
                commit,
                previous_head,
            ],
            check=False,
        )
        if update.returncode != 0:
            raise MergeQueueError(
                "integration ref changed concurrently or could not be atomically advanced"
            )

        resolved = self._resolve_commit(self._integration_ref, label="integration ref")
        if resolved != commit:
            raise MergeQueueError("integration ref does not point at the newly created commit")
        self._verify_integration_commit(
            commit=commit,
            previous_head=previous_head,
            task_commit=result.commit_sha or "",
        )

        self._head = commit
        self._integrated.append(result.task_id)
        self._attempts.append(
            MergeQueueAttempt(
                sequence=len(self._attempts),
                task_id=result.task_id,
                task_branch=result.branch_name or "",
                task_base_commit=result.base_commit or "",
                task_commit=result.commit_sha or "",
                previous_integration_commit=previous_head,
                outcome=MergeAttemptOutcome.INTEGRATED,
                integration_commit=commit,
            )
        )
        self._assert_base_clean()
        return True

    def _validate_worker_result(self, result: WorkerTaskResult) -> None:
        if result.scheduler_state is not TaskScheduleState.SUCCEEDED:
            raise MergeQueueError(f"only successful worker results may integrate: {result.task_id}")
        if self._scheduler.state(result.task_id) is not TaskScheduleState.SUCCEEDED:
            raise MergeQueueError(
                f"scheduler task must still be SUCCEEDED before integration: {result.task_id}"
            )
        if not result.branch_name or not result.base_commit or not result.commit_sha:
            raise MergeQueueError(
                f"successful worker result lacks branch/base/commit evidence: {result.task_id}"
            )
        if not result.worktree_path:
            raise MergeQueueError(
                f"successful worker result lacks deterministic worktree identity: {result.task_id}"
            )

        self._require_full_oid(result.base_commit, label="task base commit")
        self._require_full_oid(result.commit_sha, label="task commit")
        record = self._worktrees.record_for(result.task_id, base_commit=result.base_commit)
        if result.branch_name != record.branch_name:
            raise MergeQueueError(
                f"worker branch does not match the manager-owned task branch: {result.task_id}"
            )
        if Path(result.worktree_path).resolve(strict=False) != record.path.resolve(strict=False):
            raise MergeQueueError(
                f"worker path does not match the manager-owned task worktree: {result.task_id}"
            )

        branch_commit = self._resolve_commit(
            f"refs/heads/{record.branch_name}",
            label=f"task branch for {result.task_id}",
        )
        if branch_commit != result.commit_sha:
            raise MergeQueueError(
                f"task branch moved after worker finalization: {result.task_id}"
            )

        parents = self._commit_parents(result.commit_sha)
        if parents != (result.base_commit,):
            raise MergeQueueError(
                f"task commit must have exactly its assigned task base as parent: {result.task_id}"
            )

    def _assert_global_topological_gate(
        self,
        task_id: str,
        simulated_integrated: set[str],
    ) -> None:
        index = self._order_index[task_id]
        for earlier_task_id in self._order[:index]:
            if earlier_task_id in simulated_integrated:
                continue
            state = self._scheduler.state(earlier_task_id)
            if state in _TERMINAL_NON_SUCCESS:
                continue
            raise MergeQueueError(
                f"task {task_id} cannot integrate before earlier topological task "
                f"{earlier_task_id} is integrated or reaches FAILED/BLOCKED"
            )

    def _initialize_or_recover_ref(self) -> str:
        existing = self._git(
            ["show-ref", "--verify", "--quiet", self._integration_ref],
            check=False,
        )
        if existing.returncode == 1:
            zero_oid = "0" * len(self._run_base)
            created = self._git(
                ["update-ref", self._integration_ref, self._run_base, zero_oid],
                check=False,
            )
            if created.returncode != 0:
                raise MergeQueueError("integration ref could not be created atomically")
            return self._run_base
        if existing.returncode != 0:
            raise MergeQueueError("Git could not determine integration-ref existence")

        head = self._resolve_commit(self._integration_ref, label="existing integration ref")
        ancestry = self._git(
            ["merge-base", "--is-ancestor", self._run_base, head],
            check=False,
        )
        if ancestry.returncode != 0:
            raise MergeQueueError("existing integration ref does not descend from the frozen run base")
        self._recover_successful_history(head)
        return head

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
                raise MergeQueueError("existing integration history integrates a task more than once")
            if self._order_index[task_id] <= last_index:
                raise MergeQueueError("existing integration history violates deterministic DAG order")
            if parents[1] != task_commit:
                raise MergeQueueError("existing integration history task parent does not match metadata")

            record = self._worktrees.record_for(task_id, base_commit=task_base)
            if task_branch != record.branch_name:
                raise MergeQueueError("existing integration history records an unexpected task branch")
            if self._commit_parents(task_commit) != (task_base,):
                raise MergeQueueError("recovered task commit does not match its recorded task base")
            node = self._scheduler.dag.node(task_id)
            if not set(node.depends_on).issubset(recovered):
                raise MergeQueueError("existing integration history violates task dependencies")

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
            self._integrated.append(task_id)
            recovered.add(task_id)
            last_index = self._order_index[task_id]
            expected_previous = commit

        if expected_previous != head:
            raise MergeQueueError("existing integration history could not recover its final head")

    def _parse_integration_message(self, commit: str) -> dict[str, str]:
        message = self._git(["show", "-s", "--format=%B", commit]).stdout
        prefixes = {
            "DevFlow-Task: ": "task",
            "DevFlow-Task-Branch: ": "branch",
            "DevFlow-Task-Base: ": "base",
            "DevFlow-Task-Commit: ": "commit",
        }
        metadata: dict[str, str] = {}
        for line in message.splitlines():
            for prefix, key in prefixes.items():
                if line.startswith(prefix):
                    if key in metadata:
                        raise MergeQueueError("integration commit contains duplicate DevFlow metadata")
                    metadata[key] = line[len(prefix) :].strip()
        if set(metadata) != set(prefixes.values()):
            raise MergeQueueError("integration commit is missing required DevFlow metadata")
        self._require_full_oid(metadata["base"], label="recovered task base")
        self._require_full_oid(metadata["commit"], label="recovered task commit")
        return metadata

    def _integration_message(self, result: WorkerTaskResult) -> str:
        return (
            f"DevFlow integrate {result.task_id}\n\n"
            f"DevFlow-Task: {result.task_id}\n"
            f"DevFlow-Task-Branch: {result.branch_name}\n"
            f"DevFlow-Task-Base: {result.base_commit}\n"
            f"DevFlow-Task-Commit: {result.commit_sha}"
        )

    def _verify_integration_commit(
        self,
        *,
        commit: str,
        previous_head: str,
        task_commit: str,
    ) -> None:
        if self._commit_parents(commit) != (previous_head, task_commit):
            raise MergeQueueError("integration commit does not preserve the expected two-parent history")

    def _assert_ref_head(self) -> None:
        current = self._resolve_commit(self._integration_ref, label="integration ref")
        if current != self._head:
            raise MergeQueueError("integration ref moved outside the merge queue")

    def _assert_base_clean(self) -> None:
        if self._base_workspace.changed_files():
            raise MergeQueueError("integration operation unexpectedly dirtied the base workspace")

    def _validate_ref_name(self, ref_name: str) -> None:
        result = self._git(["check-ref-format", ref_name], check=False)
        if result.returncode != 0:
            raise ValueError("generated integration ref is not a valid Git ref")

    def _assert_commit_exists(self, commit: str, *, label: str) -> None:
        self._resolve_commit(commit, label=label)

    def _resolve_commit(self, ref: str, *, label: str) -> str:
        result = self._git(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
        resolved = result.stdout.strip()
        if result.returncode != 0 or _COMMIT_PATTERN.fullmatch(resolved) is None:
            raise MergeQueueError(f"{label} does not resolve to a full commit id")
        return resolved

    def _commit_parents(self, commit: str) -> tuple[str, ...]:
        line = self._git(["rev-list", "--parents", "-n", "1", commit]).stdout.strip()
        values = line.split()
        if not values or values[0] != commit:
            raise MergeQueueError("Git returned inconsistent commit-parent evidence")
        return tuple(values[1:])

    def _first_oid_line(self, output: str, *, label: str) -> str:
        first = next((line.strip() for line in output.splitlines() if line.strip()), "")
        self._require_full_oid(first, label=label)
        return first

    @staticmethod
    def _require_full_oid(value: str, *, label: str) -> None:
        if _COMMIT_PATTERN.fullmatch(value) is None:
            raise MergeQueueError(f"{label} must be a full 40-64 character hexadecimal id")

    @staticmethod
    def _commit_environment() -> dict[str, str]:
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "DevFlow",
            "GIT_AUTHOR_EMAIL": "devflow@local.invalid",
            "GIT_COMMITTER_NAME": "DevFlow",
            "GIT_COMMITTER_EMAIL": "devflow@local.invalid",
        }

    @staticmethod
    def _merge_conflict_failure(
        *,
        task_id: str,
        integration_head: str,
        task_commit: str,
        stdout: str,
        stderr: str,
    ) -> FailureReport:
        raw = "\n".join(value.strip() for value in (stdout, stderr) if value.strip())
        if len(raw) > _CONFLICT_EVIDENCE_LIMIT:
            raw = raw[:_CONFLICT_EVIDENCE_LIMIT] + "\n...[truncated]"
        evidence = [
            f"task_id={task_id}",
            f"integration_head={integration_head}",
            f"task_commit={task_commit}",
            "git_merge_tree_exit_code=1",
        ]
        if raw:
            evidence.append("git_merge_tree_output=" + raw)
        return FailureReport(
            failure_type=FailureType.MERGE_CONFLICT,
            source=FailureSource.RUNTIME,
            message="Git reported a merge conflict while integrating the task commit.",
            retryable=False,
            evidence=evidence,
        )

    def _git(
        self,
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", "-C", str(self._base_workspace.root), *arguments]
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
            raise MergeQueueError("git executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise MergeQueueError("git integration command exceeded the configured timeout") from exc

        if check and completed.returncode != 0:
            raise MergeQueueError(
                "git integration command failed: "
                f"exit_code={completed.returncode}, operation={arguments[0]}"
            )
        return completed
