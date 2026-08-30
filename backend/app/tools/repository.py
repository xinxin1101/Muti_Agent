from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.context.relevance import RelevantCodeExtractor
from app.models.task import TaskContract
from app.models.tools import ToolCall, ToolDefinition, ToolErrorCode, ToolExecutionResult
from app.workspace import LocalGitWorkspace, ScopeEnforcer

_MAX_FILE_BYTES = 1_000_000
_MAX_SCAN_FILES = 1_000
_MAX_LIST_ENTRIES = 200
_MAX_SEARCH_RESULTS = 50
_MAX_READ_RANGE_LINES = 400
_DEFAULT_READ_FILE_CHARS = 8_000
_MAX_READ_FILE_CHARS = 12_000
_DEFAULT_READ_FILES_CHARS = 4_000
_MAX_READ_FILES_CHARS = 8_000


class ListFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = Field(default="", max_length=500)


class ReadFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    max_chars: int = Field(default=_DEFAULT_READ_FILE_CHARS, ge=1, le=_MAX_READ_FILE_CHARS)


class ReadFilesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(min_length=1, max_length=4)
    max_chars_per_file: int = Field(
        default=_DEFAULT_READ_FILES_CHARS,
        ge=1,
        le=_MAX_READ_FILES_CHARS,
    )

    @field_validator("paths")
    @classmethod
    def reject_duplicate_paths(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("paths must not contain empty values")
        if len(normalized) != len(set(normalized)):
            raise ValueError("paths must not contain duplicates")
        return normalized


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


class ReadRangeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> ReadRangeArgs:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if self.end_line - self.start_line + 1 > _MAX_READ_RANGE_LINES:
            raise ValueError(f"read_range is limited to {_MAX_READ_RANGE_LINES} lines")
        return self


class ReadSymbolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=500)
    symbol: str = Field(min_length=1, max_length=300)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("symbol must not be empty")
        return normalized


class SearchCodeManyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str] = Field(min_length=1, max_length=6)
    directory: str = Field(default="", max_length=500)
    max_results_per_query: int = Field(default=10, ge=1, le=20)

    @field_validator("queries")
    @classmethod
    def normalize_queries(cls, values: list[str]) -> list[str]:
        normalized = [SearchCodeArgs.normalize_query(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("queries must not contain duplicates")
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
        self._relevance_extractor = RelevantCodeExtractor()
        self._handlers: dict[str, Callable[[dict], str]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "read_files": self._read_files,
            "read_range": self._read_range,
            "read_symbol": self._read_symbol,
            "search_code": self._search_code,
            "search_code_many": self._search_code_many,
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
                name="read_files",
                description=(
                    "Read multiple visible UTF-8 repository files in one call. Prefer this "
                    "for initial exploration of related files to reduce model round trips."
                ),
                parameters=ReadFilesArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="read_range",
                description=(
                    "Read a bounded line range from one visible UTF-8 repository file. "
                    "Use after search_code to avoid reading a whole file."
                ),
                parameters=ReadRangeArgs.model_json_schema(),
            ),
            ToolDefinition(
                name="read_symbol",
                description=(
                    "Read a named Python class, function, or method from one visible file, "
                    "including its import/module preamble when available."
                ),
                parameters=ReadSymbolArgs.model_json_schema(),
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
                name="search_code_many",
                description=(
                    "Search visible UTF-8 repository files for multiple exact text queries in "
                    "one repository scan. Prefer this when locating several related symbols."
                ),
                parameters=SearchCodeManyArgs.model_json_schema(),
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
        normalized_directory = args.directory.strip()
        if normalized_directory in {"", "."}:
            base = self.workspace.root
        else:
            self._assert_not_internal(normalized_directory)
            base = self.workspace.resolve_path(normalized_directory)
            # A missing directory is normal while implementing the first files of an empty
            # repository. Returning an explicit empty listing lets the agent create the
            # task-authorized path instead of spending a turn recovering from NOT_FOUND.
            if not base.exists():
                return json.dumps(
                    {
                        "files": [],
                        "truncated": False,
                        "directory_exists": False,
                    },
                    ensure_ascii=False,
                )
            if not base.is_dir():
                raise RepositoryToolError(
                    ToolErrorCode.INVALID_ARGUMENTS,
                    f"Path is not a directory: {normalized_directory}",
                )
        files: list[str] = []

        for path in self._iter_files(base):
            relative = path.relative_to(self.workspace.root).as_posix()
            if self._can_read(relative):
                files.append(relative)
            if len(files) >= _MAX_LIST_ENTRIES:
                break

        return json.dumps(
            {
                "files": files,
                "truncated": len(files) >= _MAX_LIST_ENTRIES,
                "directory_exists": True,
            },
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

    def _read_files(self, arguments: dict) -> str:
        args = ReadFilesArgs.model_validate(arguments)
        files: list[dict[str, object]] = []
        for requested_path in args.paths:
            relative, path = self._resolve_file_for_read(requested_path)
            text = self._read_text_file(path)
            files.append(
                {
                    "path": relative,
                    "content": text[: args.max_chars_per_file],
                    "truncated": len(text) > args.max_chars_per_file,
                }
            )
        return json.dumps({"files": files}, ensure_ascii=False)

    def _read_range(self, arguments: dict) -> str:
        args = ReadRangeArgs.model_validate(arguments)
        relative, path = self._resolve_file_for_read(args.path)
        lines = self._read_text_file(path).splitlines(keepends=True)
        if args.start_line > len(lines):
            raise RepositoryToolError(
                ToolErrorCode.NOT_FOUND,
                f"start_line is outside the file: {args.start_line}",
            )
        end_line = min(args.end_line, len(lines))
        content = "".join(lines[args.start_line - 1 : end_line])
        if len(content) > _MAX_READ_FILE_CHARS:
            content = content[:_MAX_READ_FILE_CHARS]
            truncated = True
        else:
            truncated = end_line < args.end_line
        return json.dumps(
            {
                "path": relative,
                "start_line": args.start_line,
                "end_line": end_line,
                "content": content,
                "truncated": truncated,
            },
            ensure_ascii=False,
        )

    def _read_symbol(self, arguments: dict) -> str:
        args = ReadSymbolArgs.model_validate(arguments)
        relative, path = self._resolve_file_for_read(args.path)
        if not relative.endswith(".py"):
            raise RepositoryToolError(
                ToolErrorCode.INVALID_ARGUMENTS,
                "read_symbol currently supports Python files only; use read_range instead",
            )
        source = self._read_text_file(path)
        located = self._relevance_extractor.resolve_symbol(
            path=relative,
            source=source,
            symbol=args.symbol,
        )
        if located is None:
            raise RepositoryToolError(
                ToolErrorCode.NOT_FOUND,
                f"Python symbol was not found or is ambiguous: {args.symbol}",
            )
        preamble_end, region = located
        lines = source.splitlines(keepends=True)
        preamble = "".join(lines[:preamble_end]) if preamble_end else ""
        body = "".join(lines[region.start_line - 1 : region.end_line])
        content = preamble + ("\n" if preamble and body else "") + body
        truncated = len(content) > _MAX_READ_FILE_CHARS
        return json.dumps(
            {
                "path": relative,
                "symbol": region.symbol,
                "start_line": region.start_line,
                "end_line": region.end_line,
                "content": content[:_MAX_READ_FILE_CHARS],
                "truncated": truncated,
            },
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

    def _search_code_many(self, arguments: dict) -> str:
        args = SearchCodeManyArgs.model_validate(arguments)
        base = self._resolve_directory(args.directory)
        matches_by_query: dict[str, list[dict[str, object]]] = {query: [] for query in args.queries}
        truncated_queries: set[str] = set()

        for path in self._iter_files(base):
            relative = path.relative_to(self.workspace.root).as_posix()
            if not self._can_read(relative) or path.stat().st_size > _MAX_FILE_BYTES:
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                for query in args.queries:
                    matches = matches_by_query[query]
                    if query not in line:
                        continue
                    if len(matches) >= args.max_results_per_query:
                        truncated_queries.add(query)
                        continue
                    matches.append({"path": relative, "line": line_number, "text": line[:300]})

        return json.dumps(
            {
                "results": [
                    {
                        "query": query,
                        "matches": matches_by_query[query],
                        "truncated": query in truncated_queries,
                    }
                    for query in args.queries
                ]
            },
            ensure_ascii=False,
        )

    def _write_file(self, arguments: dict) -> str:
        args = WriteFileArgs.model_validate(arguments)
        relative, path = self._resolve_for_write(args.path)
        encoded_size = len(args.content.encode("utf-8"))
        if encoded_size > _MAX_FILE_BYTES:
            raise RepositoryToolError(
                ToolErrorCode.IO_ERROR,
                "file content exceeds the V0.1 text-file size limit",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.content, encoding="utf-8")
        return json.dumps(
            {"path": relative, "bytes": encoded_size},
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
