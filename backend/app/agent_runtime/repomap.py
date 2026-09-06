from __future__ import annotations

import ast
import json

from pydantic import BaseModel, ConfigDict, Field

from app.models.tools import ToolCall
from app.tools import RepositoryToolbox

_MAX_REPO_MAP_FILES = 40
_MAX_SYMBOLS_PER_FILE = 16
_MAX_IMPORTS_PER_FILE = 10
_MAX_PROMPT_CHARS = 6_000


class RepositoryMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=500)
    symbols: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_SYMBOLS_PER_FILE)
    imports: tuple[str, ...] = Field(default_factory=tuple, max_length=_MAX_IMPORTS_PER_FILE)


class RepositoryMap(BaseModel):
    """Small deterministic navigation index. It never carries source bodies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: tuple[RepositoryMapEntry, ...] = Field(default_factory=tuple)
    visible_files: int = Field(default=0, ge=0)
    indexed_files: int = Field(default=0, ge=0)
    listing_truncated: bool = False

    def prompt_section(self) -> str:
        if not self.entries:
            return ""
        lines = [
            "Deterministic Repository Map (navigation only; no source bodies):",
            (
                f"visible_files={self.visible_files};indexed_python_files={self.indexed_files};"
                f"listing_truncated={str(self.listing_truncated).lower()}"
            ),
            "Use this map only to choose read_symbol/read_range/search_code targets.",
        ]
        for entry in self.entries:
            symbols = ",".join(entry.symbols) or "-"
            imports = ",".join(entry.imports) or "-"
            lines.append(f"{entry.path} | symbols={symbols} | imports={imports}")
        return "\n".join(lines)[:_MAX_PROMPT_CHARS]


def build_repository_map(toolbox: RepositoryToolbox) -> RepositoryMap:
    listing = toolbox.execute(
        ToolCall(
            id="runtime-repomap-list",
            name="list_files",
            arguments="{}",
        )
    )
    if not listing.ok:
        return RepositoryMap()

    try:
        listing_payload = json.loads(listing.content)
    except json.JSONDecodeError:
        return RepositoryMap()
    raw_files = listing_payload.get("files")
    if not isinstance(raw_files, list):
        return RepositoryMap()

    visible_files = [item for item in raw_files if isinstance(item, str)]
    python_files = [path for path in visible_files if path.endswith(".py")]
    prioritized = _prioritize_paths(toolbox, python_files)[:_MAX_REPO_MAP_FILES]
    entries: list[RepositoryMapEntry] = []
    for index, path in enumerate(prioritized):
        result = toolbox.execute(
            ToolCall(
                id=f"runtime-repomap-read-{index}",
                name="read_file",
                arguments=json.dumps(
                    {"path": path, "max_chars": 12_000},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
        if not result.ok:
            continue
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            continue
        source = payload.get("content")
        if not isinstance(source, str):
            continue
        entries.append(_entry_from_source(path, source))

    return RepositoryMap(
        entries=tuple(entries),
        visible_files=len(visible_files),
        indexed_files=len(entries),
        listing_truncated=bool(listing_payload.get("truncated", False)),
    )


def _prioritize_paths(toolbox: RepositoryToolbox, python_files: list[str]) -> list[str]:
    visible = set(python_files)
    ordered: list[str] = []
    preferred = [
        *toolbox.workspace.changed_files(),
        *toolbox.task.writable_files,
        *toolbox.task.readonly_files,
    ]
    for path in preferred:
        if any(character in path for character in "*?["):
            continue
        if path in visible and path not in ordered:
            ordered.append(path)
    for path in sorted(python_files):
        if path not in ordered:
            ordered.append(path)
    return ordered


def _entry_from_source(path: str, source: str) -> RepositoryMapEntry:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return RepositoryMapEntry(path=path)

    symbols: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(node.name)
        elif isinstance(node, ast.ClassDef):
            symbols.append(node.name)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(f"{node.name}.{item.name}")
                    if len(symbols) >= _MAX_SYMBOLS_PER_FILE:
                        break
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported = ",".join(alias.name for alias in node.names[:4])
            imports.append(f"{module}:{imported}" if module else imported)

        if (
            len(symbols) >= _MAX_SYMBOLS_PER_FILE
            and len(imports) >= _MAX_IMPORTS_PER_FILE
        ):
            break

    return RepositoryMapEntry(
        path=path,
        symbols=tuple(symbols[:_MAX_SYMBOLS_PER_FILE]),
        imports=tuple(imports[:_MAX_IMPORTS_PER_FILE]),
    )
