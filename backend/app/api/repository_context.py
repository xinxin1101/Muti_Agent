from __future__ import annotations

import re
import subprocess
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from uuid import UUID

from app.workspace import LocalGitWorkspace, WorkspaceGitError

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
_ALWAYS_NAMES = {
    "README.md",
    "README.rst",
    "README.txt",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
    ".github/workflows",
}
_SUMMARY_VERSION = "repository_summary_v1"


class RepositoryPlanningContextBuilder:
    """Read repository facts from one immutable commit without mutating the workspace."""

    def __init__(
        self,
        *,
        max_files: int = 32,
        max_file_chars: int = 6_000,
        max_context_chars: int = 80_000,
        max_cached_summaries: int = 64,
    ) -> None:
        if not 1 <= max_files <= 100:
            raise ValueError("max_files must be between 1 and 100")
        self._max_files = max_files
        self._max_file_chars = max_file_chars
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
        selected: list[str] = []
        for path in files:
            if self._always_include(path) and path not in selected:
                selected.append(path)

        tokens = self._requirement_tokens(requirement)
        for token in tokens:
            if len(selected) >= self._max_files:
                break
            for path in files:
                if token.lower() in path.lower() and path not in selected:
                    selected.append(path)
                    if len(selected) >= self._max_files:
                        break

        for token in tokens[:8]:
            if len(selected) >= self._max_files:
                break
            for path in self._grep_paths(root, base_commit, token):
                if path not in selected:
                    selected.append(path)
                    if len(selected) >= self._max_files:
                        break

        context_is_partial = len(selected) < len(files)
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
            f"selected_context_file_count={len(selected)}\n"
            f"context_is_partial={str(context_is_partial).lower()}\n"
            "Important: absence from this bounded context does not prove a file is absent.\n"
        )
        chunks = [summary, "\n", header, "\nSelected repository files:\n"]
        for path in selected[: self._max_files]:
            if sum(len(item) for item in chunks) >= self._max_context_chars:
                break
            chunks.append(f"\n--- {path} ---\n")
            content = self._show_file(root, base_commit, path)
            chunks.append(content[: self._max_file_chars])
            if len(content) > self._max_file_chars:
                chunks.append("\n...<file truncated by DevFlow>\n")
        result = "".join(chunks)
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

    @staticmethod
    def _build_summary(*, base_commit: str, files: tuple[str, ...]) -> str:
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
                f"directory_index={','.join(files[:80])}",
            )
        )

    @staticmethod
    def _always_include(path: str) -> bool:
        name = Path(path).name
        return name in _ALWAYS_NAMES or path.startswith(".github/workflows/")

    @staticmethod
    def _requirement_tokens(requirement: str) -> list[str]:
        ignored = {
            "add",
            "and",
            "the",
            "with",
            "for",
            "from",
            "into",
            "implement",
            "update",
            "change",
            "create",
        }
        result: list[str] = []
        for token in _TOKEN_RE.findall(requirement):
            normalized = token.lower()
            if normalized in ignored or normalized in result:
                continue
            result.append(normalized)
        return result[:20]

    def _grep_paths(self, root: Path, commit: str, token: str) -> tuple[str, ...]:
        result = self._run(
            root,
            ["grep", "-I", "-l", "--fixed-strings", "-i", token, commit, "--"],
            allow_no_match=True,
        )
        if result.returncode == 1:
            return ()
        paths: list[str] = []
        prefix = f"{commit}:"
        for raw in self._stdout(result).splitlines():
            path = raw[len(prefix) :] if raw.startswith(prefix) else raw
            if path and path not in paths:
                paths.append(path)
        return tuple(paths[: self._max_files])

    def _show_file(self, root: Path, commit: str, path: str) -> str:
        result = self._run(root, ["show", f"{commit}:{path}"], allow_no_match=True)
        if result.returncode != 0:
            return "<unavailable or non-text repository object>\n"
        content = self._stdout(result)
        if "\0" in content:
            return "<binary repository object omitted from planning context>\n"
        return content

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
