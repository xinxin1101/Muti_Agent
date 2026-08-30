from __future__ import annotations

import ast
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from app.models.context import ContextScopeKind
from app.models.task import TaskContract


class RelevantRegionKind(StrEnum):
    MODULE_PREAMBLE = "module_preamble"
    SYMBOL = "symbol"


@dataclass(frozen=True)
class RelevanceCandidate:
    path: str
    changed: bool
    scope_kinds: tuple[ContextScopeKind, ...]


@dataclass(frozen=True)
class RelevantCodeRegion:
    start_line: int
    end_line: int
    kind: RelevantRegionKind
    symbol: str | None
    score: int
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RelevantFileSelection:
    path: str
    score: int
    regions: tuple[RelevantCodeRegion, ...]
    local_dependencies: tuple[str, ...]
    evidence: tuple[str, ...]
    python_ast_indexed: bool


@dataclass(frozen=True)
class _Symbol:
    name: str
    qualname: str
    start_line: int
    end_line: int
    top_level: bool


@dataclass(frozen=True)
class _ImportRef:
    module: str
    names: tuple[str, ...]
    level: int


@dataclass(frozen=True)
class _PythonIndex:
    path: str
    module_name: str
    preamble_end_line: int
    symbols: tuple[_Symbol, ...]
    imports: tuple[_ImportRef, ...]


_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "without",
        "task",
        "code",
        "file",
        "files",
        "implementation",
        "implement",
        "change",
        "update",
        "add",
        "ensure",
    }
)
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


class RelevantCodeExtractor:
    """Deterministically rank Python regions using AST and visible local imports."""

    def __init__(
        self,
        *,
        max_index_files: int = 256,
        max_dependency_files: int = 64,
        max_regions_per_file: int = 4,
    ) -> None:
        if not 1 <= max_index_files <= 2_000:
            raise ValueError("max_index_files must be between 1 and 2000")
        if not 0 <= max_dependency_files <= 512:
            raise ValueError("max_dependency_files must be between 0 and 512")
        if not 1 <= max_regions_per_file <= 16:
            raise ValueError("max_regions_per_file must be between 1 and 16")
        self._max_index_files = max_index_files
        self._max_dependency_files = max_dependency_files
        self._max_regions_per_file = max_regions_per_file

    def select(
        self,
        task: TaskContract,
        candidates: Sequence[RelevanceCandidate],
        *,
        load_source: Callable[[str], str | None],
    ) -> list[RelevantFileSelection]:
        task_terms = self._task_terms(task)
        candidate_by_path = {candidate.path: candidate for candidate in candidates}
        module_paths = self._module_path_index(candidate_by_path)
        base_scores = {
            candidate.path: self._base_score(candidate, task_terms) for candidate in candidates
        }

        indexes = self._initial_indexes(
            candidates,
            task_terms=task_terms,
            load_source=load_source,
        )
        dependency_symbols: dict[str, set[str]] = defaultdict(set)
        dependency_parents: dict[str, set[str]] = defaultdict(set)
        local_dependencies: dict[str, set[str]] = defaultdict(set)
        dependency_queue: set[str] = set()

        for path in sorted(indexes):
            for import_ref in indexes[path].imports:
                dependency_path = self._resolve_local_import(
                    indexes[path],
                    import_ref,
                    module_paths=module_paths,
                )
                if dependency_path is None or dependency_path == path:
                    continue
                local_dependencies[path].add(dependency_path)
                dependency_parents[dependency_path].add(path)
                dependency_symbols[dependency_path].update(import_ref.names)
                if dependency_path not in indexes:
                    dependency_queue.add(dependency_path)

        self._index_dependencies(
            dependency_queue,
            indexes=indexes,
            load_source=load_source,
        )

        scores = dict(base_scores)
        file_evidence: dict[str, list[str]] = {
            candidate.path: list(self._base_evidence(candidate, task_terms))
            for candidate in candidates
        }
        self._apply_dependency_scores(
            scores,
            file_evidence=file_evidence,
            dependency_parents=dependency_parents,
            dependency_symbols=dependency_symbols,
        )

        selections: list[RelevantFileSelection] = []
        for candidate in candidates:
            index = indexes.get(candidate.path)
            regions: tuple[RelevantCodeRegion, ...] = ()
            if index is not None:
                regions = self._regions_for_index(
                    index,
                    candidate=candidate,
                    task_terms=task_terms,
                    imported_symbols=dependency_symbols.get(candidate.path, set()),
                )
                if regions:
                    scores[candidate.path] += max(region.score for region in regions)
                    file_evidence[candidate.path].append("python_ast_regions")

            selections.append(
                RelevantFileSelection(
                    path=candidate.path,
                    score=scores[candidate.path],
                    regions=regions,
                    local_dependencies=tuple(sorted(local_dependencies.get(candidate.path, set()))),
                    evidence=tuple(file_evidence[candidate.path]),
                    python_ast_indexed=index is not None,
                )
            )

        return sorted(selections, key=lambda item: (-item.score, item.path))

    def resolve_symbol(
        self,
        *,
        path: str,
        source: str,
        symbol: str,
    ) -> tuple[int, RelevantCodeRegion] | None:
        """Resolve one Python symbol for progressive tool reads.

        This reuses the same AST index used by context selection.  Exact qualified names are
        preferred; a bare name is accepted only when it maps to exactly one symbol.
        """

        index = self._index_python(path, source)
        if index is None:
            return None
        requested = symbol.strip()
        exact = [item for item in index.symbols if item.qualname == requested]
        matches = exact or [item for item in index.symbols if item.name == requested]
        if len(matches) != 1:
            return None
        match = matches[0]
        return (
            index.preamble_end_line,
            RelevantCodeRegion(
                start_line=match.start_line,
                end_line=match.end_line,
                kind=RelevantRegionKind.SYMBOL,
                symbol=match.qualname,
                score=0,
                evidence=("explicit_tool_symbol_request",),
            ),
        )

    def _initial_indexes(
        self,
        candidates: Sequence[RelevanceCandidate],
        *,
        task_terms: frozenset[str],
        load_source: Callable[[str], str | None],
    ) -> dict[str, _PythonIndex]:
        python_candidates = [
            candidate for candidate in candidates if candidate.path.endswith(".py")
        ]
        index_order = sorted(
            python_candidates,
            key=lambda candidate: (
                -self._seed_priority(candidate, task_terms),
                candidate.path,
            ),
        )[: self._max_index_files]

        indexes: dict[str, _PythonIndex] = {}
        for candidate in index_order:
            source = load_source(candidate.path)
            if source is None:
                continue
            index = self._index_python(candidate.path, source)
            if index is not None:
                indexes[candidate.path] = index
        return indexes

    def _index_dependencies(
        self,
        paths: set[str],
        *,
        indexes: dict[str, _PythonIndex],
        load_source: Callable[[str], str | None],
    ) -> None:
        remaining = self._max_dependency_files
        for path in sorted(paths):
            if remaining <= 0:
                return
            source = load_source(path)
            if source is None:
                continue
            index = self._index_python(path, source)
            if index is None:
                continue
            indexes[path] = index
            remaining -= 1

    @staticmethod
    def _apply_dependency_scores(
        scores: dict[str, int],
        *,
        file_evidence: dict[str, list[str]],
        dependency_parents: dict[str, set[str]],
        dependency_symbols: dict[str, set[str]],
    ) -> None:
        for dependency_path, parents in sorted(dependency_parents.items()):
            if dependency_path not in scores:
                continue
            strongest_parent = max(scores.get(parent, 0) for parent in parents)
            scores[dependency_path] = max(
                scores[dependency_path],
                max(0, strongest_parent - 1_500),
            )
            file_evidence[dependency_path].append(f"local_import_from={','.join(sorted(parents))}")
            names = sorted(name for name in dependency_symbols[dependency_path] if name != "*")
            if names:
                file_evidence[dependency_path].append(f"imported_symbols={','.join(names)}")

    def _regions_for_index(
        self,
        index: _PythonIndex,
        *,
        candidate: RelevanceCandidate,
        task_terms: frozenset[str],
        imported_symbols: set[str],
    ) -> tuple[RelevantCodeRegion, ...]:
        regions: list[RelevantCodeRegion] = []
        if index.preamble_end_line > 0:
            regions.append(
                RelevantCodeRegion(
                    start_line=1,
                    end_line=index.preamble_end_line,
                    kind=RelevantRegionKind.MODULE_PREAMBLE,
                    symbol=None,
                    score=80,
                    evidence=("module_docstring_or_import_preamble",),
                )
            )

        imported_terms = {
            term for name in imported_symbols for term in self._identifier_terms(name)
        }
        scored_symbols: list[tuple[int, _Symbol, tuple[str, ...]]] = []
        for symbol in index.symbols:
            symbol_terms = self._identifier_terms(symbol.qualname)
            task_overlap = sorted(symbol_terms & task_terms)
            import_overlap = sorted(symbol_terms & imported_terms)
            relevance_score = len(task_overlap) * 240 + len(import_overlap) * 360
            if relevance_score <= 0:
                continue

            evidence: list[str] = []
            if task_overlap:
                evidence.append(f"task_terms={','.join(task_overlap)}")
            if import_overlap:
                evidence.append(f"import_terms={','.join(import_overlap)}")
            score = relevance_score + (20 if symbol.top_level else 0)
            scored_symbols.append((score, symbol, tuple(evidence)))

        if not scored_symbols and (
            candidate.changed or ContextScopeKind.WRITABLE in candidate.scope_kinds
        ):
            for symbol in [item for item in index.symbols if item.top_level][
                : self._max_regions_per_file
            ]:
                scored_symbols.append((40, symbol, ("writable_or_changed_top_level_fallback",)))

        scored_symbols.sort(key=lambda item: (-item[0], item[1].start_line, item[1].qualname))
        selected_symbols: list[tuple[int, _Symbol, tuple[str, ...]]] = []
        for item in scored_symbols:
            _, symbol, _ = item
            if any(
                self._ranges_overlap(symbol, selected_symbol)
                for _, selected_symbol, _ in selected_symbols
            ):
                continue
            selected_symbols.append(item)
            if len(selected_symbols) >= self._max_regions_per_file:
                break

        for score, symbol, evidence in selected_symbols:
            regions.append(
                RelevantCodeRegion(
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    kind=RelevantRegionKind.SYMBOL,
                    symbol=symbol.qualname,
                    score=score,
                    evidence=evidence,
                )
            )
        return self._merge_adjacent_regions(regions)

    @staticmethod
    def _ranges_overlap(left: _Symbol, right: _Symbol) -> bool:
        return not (left.end_line < right.start_line or right.end_line < left.start_line)

    @staticmethod
    def _merge_adjacent_regions(
        regions: Sequence[RelevantCodeRegion],
    ) -> tuple[RelevantCodeRegion, ...]:
        if not regions:
            return ()
        ordered = sorted(regions, key=lambda region: (region.start_line, region.end_line))
        merged: list[RelevantCodeRegion] = [ordered[0]]
        for region in ordered[1:]:
            previous = merged[-1]
            if region.start_line > previous.end_line + 1:
                merged.append(region)
                continue
            merged[-1] = RelevantCodeRegion(
                start_line=previous.start_line,
                end_line=max(previous.end_line, region.end_line),
                kind=(
                    RelevantRegionKind.SYMBOL
                    if previous.symbol or region.symbol
                    else RelevantRegionKind.MODULE_PREAMBLE
                ),
                symbol=previous.symbol or region.symbol,
                score=max(previous.score, region.score),
                evidence=tuple(sorted(set(previous.evidence + region.evidence))),
            )
        return tuple(merged)

    def _index_python(self, path: str, source: str) -> _PythonIndex | None:
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError):
            return None

        preamble_end = 0
        imports: list[_ImportRef] = []
        for position, node in enumerate(tree.body):
            if (
                position == 0
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                preamble_end = max(
                    preamble_end,
                    getattr(node, "end_lineno", node.lineno),
                )
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                preamble_end = max(
                    preamble_end,
                    getattr(node, "end_lineno", node.lineno),
                )

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    _ImportRef(module=alias.name, names=(), level=0) for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    _ImportRef(
                        module=node.module or "",
                        names=tuple(alias.name for alias in node.names),
                        level=node.level,
                    )
                )

        symbols: list[_Symbol] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(self._symbol(node, qualname=node.name, top_level=True))
            if isinstance(node, ast.ClassDef):
                symbols.extend(
                    self._symbol(
                        child,
                        qualname=f"{node.name}.{child.name}",
                        top_level=False,
                    )
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                )

        return _PythonIndex(
            path=path,
            module_name=self._canonical_module(path),
            preamble_end_line=preamble_end,
            symbols=tuple(symbols),
            imports=tuple(imports),
        )

    @staticmethod
    def _symbol(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        *,
        qualname: str,
        top_level: bool,
    ) -> _Symbol:
        return _Symbol(
            name=node.name,
            qualname=qualname,
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno),
            top_level=top_level,
        )

    def _resolve_local_import(
        self,
        index: _PythonIndex,
        import_ref: _ImportRef,
        *,
        module_paths: dict[str, tuple[str, ...]],
    ) -> str | None:
        module = self._absolute_import_module(index, import_ref)
        candidate_modules = [module] if module else []
        for name in import_ref.names:
            if module and name != "*":
                candidate_modules.append(f"{module}.{name}")

        for candidate_module in candidate_modules:
            for alias in self._module_suffixes(candidate_module):
                matches = module_paths.get(alias, ())
                if len(matches) == 1:
                    return matches[0]
                if len(matches) > 1:
                    return None
        return None

    @staticmethod
    def _absolute_import_module(index: _PythonIndex, import_ref: _ImportRef) -> str:
        if not import_ref.level:
            return import_ref.module

        current_parts = index.module_name.split(".") if index.module_name else []
        package_parts = current_parts if index.path.endswith("/__init__.py") else current_parts[:-1]
        levels_up = max(import_ref.level - 1, 0)
        if levels_up > len(package_parts):
            return ""
        if levels_up:
            package_parts = package_parts[: len(package_parts) - levels_up]
        module_parts = import_ref.module.split(".") if import_ref.module else []
        return ".".join([*package_parts, *module_parts])

    def _module_path_index(
        self,
        candidates: dict[str, RelevanceCandidate],
    ) -> dict[str, tuple[str, ...]]:
        paths_by_module: dict[str, set[str]] = defaultdict(set)
        for path in sorted(candidates):
            if not path.endswith(".py"):
                continue
            for alias in self._module_suffixes(self._canonical_module(path)):
                paths_by_module[alias].add(path)
        return {module: tuple(sorted(paths)) for module, paths in paths_by_module.items()}

    @staticmethod
    def _canonical_module(path: str) -> str:
        parts = list(PurePosixPath(path).with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    @staticmethod
    def _module_suffixes(module: str) -> tuple[str, ...]:
        parts = [part for part in module.split(".") if part]
        return tuple(".".join(parts[index:]) for index in range(len(parts)))

    def _task_terms(self, task: TaskContract) -> frozenset[str]:
        text = "\n".join([task.objective, *task.acceptance_criteria])
        terms = set(self._text_terms(text))
        for pattern in [*task.writable_files, *task.readable_files, *task.readonly_files]:
            terms.update(self._text_terms(pattern))
        return frozenset(term for term in terms if term not in _STOP_WORDS and len(term) > 1)

    def _base_score(
        self,
        candidate: RelevanceCandidate,
        task_terms: frozenset[str],
    ) -> int:
        score = 0
        if candidate.changed:
            score += 10_000
        if ContextScopeKind.WRITABLE in candidate.scope_kinds:
            score += 8_000
        if ContextScopeKind.READ_ONLY in candidate.scope_kinds:
            score += 3_000
        if ContextScopeKind.READABLE in candidate.scope_kinds:
            score += 1_000
        score += len(self._text_terms(candidate.path) & task_terms) * 180
        return score

    def _seed_priority(
        self,
        candidate: RelevanceCandidate,
        task_terms: frozenset[str],
    ) -> int:
        score = self._base_score(candidate, task_terms)
        if candidate.changed:
            score += 20_000
        if ContextScopeKind.WRITABLE in candidate.scope_kinds:
            score += 10_000
        return score

    def _base_evidence(
        self,
        candidate: RelevanceCandidate,
        task_terms: frozenset[str],
    ) -> tuple[str, ...]:
        evidence: list[str] = []
        if candidate.changed:
            evidence.append("changed_file")
        if ContextScopeKind.WRITABLE in candidate.scope_kinds:
            evidence.append("writable_scope")
        if ContextScopeKind.READ_ONLY in candidate.scope_kinds:
            evidence.append("read_only_scope")
        if ContextScopeKind.READABLE in candidate.scope_kinds:
            evidence.append("readable_scope")
        overlap = sorted(self._text_terms(candidate.path) & task_terms)
        if overlap:
            evidence.append(f"path_terms={','.join(overlap)}")
        return tuple(evidence)

    def _identifier_terms(self, value: str) -> set[str]:
        return self._text_terms(value.replace(".", "_"))

    @staticmethod
    def _text_terms(value: str) -> set[str]:
        terms: set[str] = set()
        for token in _TOKEN_RE.findall(value):
            for camel_piece in _CAMEL_BOUNDARY_RE.sub(" ", token).split():
                terms.update(part.lower() for part in re.split(r"_+", camel_piece) if part)
        return terms
