from __future__ import annotations

import re
import subprocess
from pathlib import Path

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


class RepositoryPlanningContextBuilder:
    """Read repository facts from one immutable commit without mutating the workspace."""

    def __init__(
        self,
        *,
        max_files: int = 32,
        max_file_chars: int = 6_000,
        max_context_chars: int = 80_000,
    ) -> None:
        if not 1 <= max_files <= 100:
            raise ValueError("max_files must be between 1 and 100")
        self._max_files = max_files
        self._max_file_chars = max_file_chars
        self._max_context_chars = max_context_chars

    def build(
        self,
        workspace: LocalGitWorkspace,
        *,
        base_commit: str,
        requirement: str,
        repository_url: str,
        default_branch: str,
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
        header = (
            f"repository_url={repository_url}\n"
            f"default_branch={default_branch}\n"
            f"base_commit={base_commit}\n"
            f"repository_file_count={len(files)}\n"
            f"selected_context_file_count={len(selected)}\n"
            f"context_is_partial={str(context_is_partial).lower()}\n"
            "Important: absence from this bounded context does not prove a file is absent.\n"
        )
        chunks = [header, "\nSelected repository files:\n"]
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
        for raw in result.stdout.splitlines():
            path = raw[len(prefix) :] if raw.startswith(prefix) else raw
            if path and path not in paths:
                paths.append(path)
        return tuple(paths[: self._max_files])

    def _show_file(self, root: Path, commit: str, path: str) -> str:
        result = self._run(root, ["show", f"{commit}:{path}"], allow_no_match=True)
        if result.returncode != 0:
            return "<unavailable or non-text repository object>\n"
        return result.stdout

    def _lines(self, root: Path, arguments: list[str]) -> tuple[str, ...]:
        result = self._run(root, arguments)
        return tuple(line for line in result.stdout.splitlines() if line.strip())

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
                timeout=15.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceGitError("repository planning context Git read failed") from exc
        if result.returncode != 0 and not (allow_no_match and result.returncode == 1):
            raise WorkspaceGitError("repository planning context Git read failed")
        return result
