from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.models.task import TaskContract
from app.models.tools import ToolCall, ToolDefinition, ToolErrorCode, ToolExecutionResult
from app.workspace import LocalGitWorkspace, ScopeEnforcer

_MAX_FILE_BYTES = 1_000_000
_MAX_SCAN_FILES = 1_000
_MAX_LIST_ENTRIES = 200
_MAX_SEARCH_RESULTS = 50


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(default="", max_length=500)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    max_chars: int = Field(default=20_000, ge=1, le=20_000)


class SearchCodeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    directory: str = Field(default="", max_length=500)
    max_results: int = Field(default=20, ge=1, le=_MAX_SEARCH_RESULTS)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class WriteFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=_MAX_FILE_BYTES)


class ApplyPatchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    old_text: str = Field(min_length=1, max_length=200_000)
    new_text: str = Field(max_length=200_000)


class RepositoryToolError(RuntimeError):
    def __init__(self, code: ToolErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class RepositoryToolbox:
    """Controlled repository tools exposed to a Developer Agent.

    The toolbox never exposes an unrestricted shell. Reads are filtered by the task's readable,
    writable, or read-only scopes. Mutations are checked against read-only and writable scopes
    before the filesystem is touched. Step 1.5 Git scope enforcement remains the post-write gate.
    """

    def __init__(
        self,
        *,
        workspace: LocalGitWorkspace,
        task: TaskContract,
        scope_enforcer: ScopeEnforcer | None = None,
    ) -> None:
        self.workspace = workspace
        self.task = task
        self.scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._handlers: dict[str, Callable[[dict], str]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_code": self._search_code,
            "write_file": self._write_file,
            "apply_patch": self._apply_patch,
        }

    @staticmethod
    def definitions() -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="list_files",
                description=(
                    "List repository files visible to this task under an optional directory. "
                    "Only readable paths are returned."
                ),
                parameters=ListFilesArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="read_file",
                description=(
                    "Read a UTF-8 repository file that is visible to this task. "
                    "The result may be truncated to the requested character limit."
                ),
                parameters=ReadFileArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="search_code",
                description=(
                    "Search visible UTF-8 repository files for an exact text query. "
                    "Returns matching path, line number, and a short line preview."
                ),
                parameters=SearchCodeArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="write_file",
                description=(
                    "Create or replace one UTF-8 repository file. The path must be writable and "
                    "must not match a read-only scope."
                ),
                parameters=WriteFileArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="apply_patch",
                description=(
                    "Apply one exact text replacement to an existing UTF-8 file. old_text must "
                    "occur exactly once. The path must be writable and not read-only."
                ),
                parameters=ApplyPatchArgs.model_json_schema(),
            ),
        ]

    def execute(self, call: ToolCall) -> ToolExecutionResult:
        handler = self._handlers.get(call.name)
        if handler is None:
            return self._error_result(
                call,
                ToolErrorCode.UNKNOWN_TOOL,
                f"Unknown repository tool: {call.name}",
            )

        try:
            raw_arguments = json.loads(call.arguments or "{}")
            if not isinstance(raw_arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
            content = handler(raw_arguments)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return self._error_result(call, ToolErrorCode.INVALID_ARGUMENTS, str(exc))
        except RepositoryToolError as exc:
            return self._error_result(call, exc.code, str(exc))
        except OSError as exc:
            return self._error_result(call, ToolErrorCode.IO_ERROR, str(exc))

        return ToolExecutionResult(
            tool_call_id=call.id,
            name=call.name,
            ok=True,
            content=content,
        )

    def _list_files(self, arguments: dict) -> str:
        args = ListFilesArgs.model_validate(arguments)
        base = self._resolve_directory(args.directory)
        files: list[str] = []

        for path in self._iter_files(base):
            relative = path.relative_to(self.workspace.root).as_posix()
            if self._can_read(relative):
                files.append(relative)
            if len(files) >= _MAX_LIST_ENTRIES:
                break

        return json.dumps(
            {"files": files, "truncated": len(files) >= _MAX_LIST_ENTRIES},
            ensure_ascii=False,
        )

    def _read_file(self, arguments: dict) -> str:
        args = ReadFileArgs.model_validate(arguments)
        relative, path = self._resolve_file_for_read(args.path)
        text = self._read_text_file(path)
        truncated = len(text) > args.max_chars
        content = text[: args.max_chars]
        return json.dumps(
            {"path": relative, "content": content, "truncated": truncated},
            ensure_ascii=False,
        )

    def _search_code(self, arguments: dict) -> str:
        args = SearchCodeArgs.model_validate(arguments)
        base = self._resolve_directory(args.directory)
        matches: list[dict[str, object]] = []

        for path in self._iter_files(base):
            relative = path.relative_to(self.workspace.root).as_posix()
            if not self._can_read(relative):
                continue
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

            for line_number, line in enumerate(text.splitlines(), start=1):
                if args.query not in line:
                    continue
                matches.append(
                    {
                        "path": relative,
                        "line": line_number,
                        "text": line[:300],
                    }
                )
                if len(matches) >= args.max_results:
                    return json.dumps(
                        {"matches": matches, "truncated": True},
                        ensure_ascii=False,
                    )

        return json.dumps({"matches": matches, "truncated": False}, ensure_ascii=False)

    def _write_file(self, arguments: dict) -> str:
        args = WriteFileArgs.model_validate(arguments)
        relative, path = self._resolve_for_write(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.content, encoding="utf-8")
        return json.dumps(
            {"path": relative, "bytes": len(args.content.encode("utf-8"))},
            ensure_ascii=False,
        )

    def _apply_patch(self, arguments: dict) -> str:
        args = ApplyPatchArgs.model_validate(arguments)
        relative, path = self._resolve_for_write(args.path)
        if not path.is_file():
            raise RepositoryToolError(
                ToolErrorCode.NOT_FOUND,
                f"Patch target does not exist: {relative}",
            )

        text = self._read_text_file(path)
        occurrences = text.count(args.old_text)
        if occurrences == 0:
            raise RepositoryToolError(
                ToolErrorCode.NOT_FOUND,
                "old_text was not found in the patch target",
            )
        if occurrences != 1:
            raise RepositoryToolError(
                ToolErrorCode.AMBIGUOUS_PATCH,
                f"old_text must occur exactly once, found {occurrences} occurrences",
            )

        updated = text.replace(args.old_text, args.new_text, 1)
        if len(updated.encode("utf-8")) > _MAX_FILE_BYTES:
            raise RepositoryToolError(
                ToolErrorCode.IO_ERROR,
                "patched file would exceed the V0.1 file-size limit",
            )
        path.write_text(updated, encoding="utf-8")
        return json.dumps({"path": relative, "replacements": 1}, ensure_ascii=False)

    def _resolve_directory(self, repository_directory: str) -> Path:
        normalized = repository_directory.strip()
        if normalized in {"", "."}:
            return self.workspace.root

        self._assert_not_internal(normalized)
        directory = self.workspace.resolve_path(normalized)
        if not directory.exists():
            raise RepositoryToolError(
                ToolErrorCode.NOT_FOUND,
                f"Directory does not exist: {normalized}",
            )
        if not directory.is_dir():
            raise RepositoryToolError(
                ToolErrorCode.INVALID_ARGUMENTS,
                f"Path is not a directory: {normalized}",
            )
        return directory

    def _resolve_file_for_read(self, repository_path: str) -> tuple[str, Path]:
        relative = repository_path.strip()
        self._assert_not_internal(relative)
        path = self.workspace.resolve_path(relative)
        if not self._can_read(relative):
            raise RepositoryToolError(
                ToolErrorCode.PATH_DENIED,
                f"Read path is outside the task's visible scopes: {relative}",
            )
        if not path.is_file():
            raise RepositoryToolError(ToolErrorCode.NOT_FOUND, f"File does not exist: {relative}")
        return relative, path

    def _resolve_for_write(self, repository_path: str) -> tuple[str, Path]:
        relative = repository_path.strip()
        self._assert_not_internal(relative)
        path = self.workspace.resolve_path(relative)

        scope_result = self.scope_enforcer.check(self.task, [relative])
        if not scope_result.passed:
            violation = scope_result.violations[0]
            raise RepositoryToolError(
                ToolErrorCode.PATH_DENIED,
                f"Write denied by {violation.kind.value}: {relative}",
            )
        if path.exists() and path.is_dir():
            raise RepositoryToolError(
                ToolErrorCode.INVALID_ARGUMENTS,
                f"Write target is a directory: {relative}",
            )
        return relative, path

    def _can_read(self, relative_path: str) -> bool:
        patterns = [
            *self.task.readable_files,
            *self.task.writable_files,
            *self.task.readonly_files,
        ]
        return any(self.scope_enforcer.matches(relative_path, pattern) for pattern in patterns)

    def _iter_files(self, base: Path):
        scanned = 0
        for path in sorted(base.rglob("*")):
            if scanned >= _MAX_SCAN_FILES:
                break
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(self.workspace.root).as_posix()
            if self._is_internal(relative):
                continue
            scanned += 1
            yield path

    @staticmethod
    def _read_text_file(path: Path) -> str:
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise RepositoryToolError(
                ToolErrorCode.IO_ERROR,
                "file exceeds the V0.1 text-file size limit",
            )
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RepositoryToolError(
                ToolErrorCode.IO_ERROR,
                "file is not valid UTF-8 text",
            ) from exc

    @classmethod
    def _assert_not_internal(cls, relative_path: str) -> None:
        if cls._is_internal(relative_path):
            raise RepositoryToolError(
                ToolErrorCode.PATH_DENIED,
                "DevFlow repository tools never expose .git internals",
            )

    @staticmethod
    def _is_internal(relative_path: str) -> bool:
        normalized = relative_path.strip().strip("/")
        return normalized == ".git" or normalized.startswith(".git/")

    @staticmethod
    def _error_result(
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id=call.id,
            name=call.name,
            ok=False,
            content=message,
            error_code=code,
        )
