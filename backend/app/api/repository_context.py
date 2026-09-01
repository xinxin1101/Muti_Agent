from __future__ import annotations

import subprocess
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from uuid import UUID

from app.workspace import LocalGitWorkspace, WorkspaceGitError

_SUMMARY_VERSION = "repository_summary_v2_metadata_only"


class RepositoryPlanningContextBuilder:
    """Build a compact, metadata-only planning view of one immutable commit.

    Planning decides package boundaries; it does not need repository source text.  Keeping source
    blobs out of this context prevents a large repository from consuming the Planner's recovery
    budget before an executable DAG exists.  Developers later receive task-scoped source context.
    """

    def __init__(
        self,
        *,
        max_directory_entries: int = 100,
        max_context_chars: int = 2_400,
        max_cached_summaries: int = 64,
    ) -> None:
        if not 1 <= max_directory_entries <= 500:
            raise ValueError("max_directory_entries must be between 1 and 500")
        if not 256 <= max_context_chars <= 16_000:
            raise ValueError("max_context_chars must be between 256 and 16000")
        self._max_directory_entries = max_directory_entries
        self._max_context_chars = max_context_chars
        if not 1 <= max_cached_summaries <= 512:
            raise ValueError("max_cached_summaries must be between 1 and 512")
        self._max_cached_summaries = max_cached_summaries
        self._summary_cache: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._cache_lock = Lock()

    @property
    def cached_summary_count(self) -> int:
        with self._cache_lock:
            return len(self._summary_cache)

    def build(
        self,
        workspace: LocalGitWorkspace,
        *,
        base_commit: str,
        requirement: str,
        repository_url: str,
        default_branch: str,
        project_id: UUID | None = None,
    ) -> str:
        root = workspace.root
        files = self._lines(root, ["ls-tree", "-r", "--name-only", base_commit])
        del requirement  # The immutable repository summary must not search or expose source blobs.
        summary = self._repository_summary(
            project_id=project_id,
            base_commit=base_commit,
            files=files,
        )
        header = (
            f"repository_url={repository_url}\n"
            f"default_branch={default_branch}\n"
            f"base_commit={base_commit}\n"
            f"repository_file_count={len(files)}\n"
            "context_kind=metadata_only\n"
            "source_files_included=false\n"
            "Important: absence from this bounded index does not prove a file is absent.\n"
        )
        result = "".join((summary, "\n", header))
        if len(result) > self._max_context_chars:
            return result[: self._max_context_chars] + "\n...<context truncated by DevFlow>"
        return result

    def _repository_summary(
        self,
        *,
        project_id: UUID | None,
        base_commit: str,
        files: tuple[str, ...],
    ) -> str:
        if project_id is None:
            return self._build_summary(base_commit=base_commit, files=files)
        key = (str(project_id), base_commit, _SUMMARY_VERSION)
        with self._cache_lock:
            cached = self._summary_cache.get(key)
            if cached is not None:
                self._summary_cache.move_to_end(key)
                return cached
        summary = self._build_summary(base_commit=base_commit, files=files)
        with self._cache_lock:
            self._summary_cache[key] = summary
            self._summary_cache.move_to_end(key)
            while len(self._summary_cache) > self._max_cached_summaries:
                self._summary_cache.popitem(last=False)
        return summary

    def _build_summary(self, *, base_commit: str, files: tuple[str, ...]) -> str:
        names = {Path(path).name for path in files}
        technologies: list[str] = []
        if {"pyproject.toml", "requirements.txt"} & names:
            technologies.append("Python")
        if {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"} & names:
            technologies.append("Node.js")
        if "go.mod" in names:
            technologies.append("Go")
        if "Cargo.toml" in names:
            technologies.append("Rust")
        dependencies = [
            path
            for path in files
            if Path(path).name
            in {
                "pyproject.toml",
                "requirements.txt",
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "go.mod",
                "Cargo.toml",
            }
        ]
        entrypoints = [
            path
            for path in files
            if Path(path).name
            in {"main.py", "app.py", "server.py", "index.js", "index.ts", "main.ts", "main.tsx"}
        ]
        test_hint = "unknown"
        if "Python" in technologies:
            test_hint = "pytest -q"
        elif "Node.js" in technologies:
            test_hint = "npm test"
        return "\n".join(
            (
                f"repository_summary_version={_SUMMARY_VERSION}",
                f"summary_base_commit={base_commit}",
                f"technology={','.join(technologies) or 'unknown'}",
                f"entry_files={','.join(entrypoints[:12]) or 'unknown'}",
                f"dependency_files={','.join(dependencies[:12]) or 'none'}",
                f"suggested_test_command={test_hint}",
                f"directory_index={','.join(files[: self._max_directory_entries])}",
            )
        )

    def _lines(self, root: Path, arguments: list[str]) -> tuple[str, ...]:
        result = self._run(root, arguments)
        return tuple(line for line in self._stdout(result).splitlines() if line.strip())

    @staticmethod
    def _stdout(result: subprocess.CompletedProcess[str]) -> str:
        """Return text defensively even if a platform reader failed before producing stdout."""

        return result.stdout if isinstance(result.stdout, str) else ""

    @staticmethod
    def _run(
        root: Path,
        arguments: list[str],
        *,
        allow_no_match: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), *arguments],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                # Git blobs are repository data, not console text. Pin decoding rather than
                # inheriting Windows' GBK locale, and replace malformed/binary bytes so one
                # non-text file cannot prevent planning from reaching the model.
                encoding="utf-8",
                errors="replace",
                timeout=15.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceGitError("repository planning context Git read failed") from exc
        if result.returncode != 0 and not (allow_no_match and result.returncode == 1):
            raise WorkspaceGitError("repository planning context Git read failed")
        return result
