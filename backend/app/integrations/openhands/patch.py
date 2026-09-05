from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.vendor.openhands.apply_patch.core import (
    ActionType,
    DiffError,
    apply_commit,
    identify_files_needed,
    load_files,
    patch_to_commit,
    text_to_patch,
)


class OpenHandsPatchError(ValueError):
    """Safe adapter error for the vendored OpenHands patch engine."""


@dataclass(frozen=True)
class OpenHandsPatchResult:
    changed_paths: tuple[str, ...]
    operations: tuple[str, ...]
    fuzz: int


class OpenHandsPatchAdapter:
    """Run the OpenHands patch parser under DevFlow-owned filesystem authority.

    The vendored parser owns patch syntax/context matching only. DevFlow callbacks remain
    authoritative for scope checks, path resolution, file-size limits, and actual mutations.
    All target paths are preflighted before apply_commit so a later denied path cannot leave a
    partially-applied multi-file patch behind.
    """

    @classmethod
    def apply(
        cls,
        *,
        patch_text: str,
        open_file: Callable[[str], str],
        preflight_write: Callable[[str], None],
        validate_content: Callable[[str, str], None],
        path_exists: Callable[[str], bool],
        write_file: Callable[[str, str], None],
        remove_file: Callable[[str], None],
    ) -> OpenHandsPatchResult:
        try:
            for path in cls._marker_paths(patch_text):
                cls._validate_relative_path(path)

            original = load_files(identify_files_needed(patch_text), open_file)
            patch, fuzz = text_to_patch(patch_text, original)
            commit = patch_to_commit(patch, original)
            if not commit.changes:
                raise DiffError("Patch produced no changes")

            changed_paths: list[str] = []
            operations: list[str] = []
            for path, change in commit.changes.items():
                cls._validate_relative_path(path)
                preflight_write(path)

                if change.type is ActionType.ADD and path_exists(path):
                    raise DiffError(f"Add File Error: File already exists: {path}")

                target_path = change.move_path or path
                if change.move_path:
                    cls._validate_relative_path(change.move_path)
                    if change.move_path == path:
                        raise DiffError(f"Move target must differ from source: {path}")
                    preflight_write(change.move_path)
                    if path_exists(change.move_path):
                        raise DiffError(f"Move target already exists: {change.move_path}")

                if change.new_content is not None:
                    validate_content(target_path, change.new_content)

                operations.append(f"{change.type.value}:{target_path}")
                changed_paths.append(path)
                if change.move_path:
                    changed_paths.append(change.move_path)

            apply_commit(commit, write_file, remove_file)
            return OpenHandsPatchResult(
                changed_paths=tuple(dict.fromkeys(changed_paths)),
                operations=tuple(operations),
                fuzz=fuzz,
            )
        except DiffError as exc:
            raise OpenHandsPatchError(str(exc)) from exc

    @staticmethod
    def _marker_paths(patch_text: str) -> tuple[str, ...]:
        prefixes = (
            "*** Update File: ",
            "*** Delete File: ",
            "*** Add File: ",
            "*** Move to: ",
        )
        paths: list[str] = []
        for line in patch_text.strip().splitlines():
            for prefix in prefixes:
                if line.startswith(prefix):
                    paths.append(line[len(prefix) :])
                    break
        return tuple(paths)

    @staticmethod
    def _validate_relative_path(path: str) -> None:
        if not path or path != path.strip():
            raise OpenHandsPatchError("Patch paths must be non-empty without edge whitespace")
        if "\\" in path:
            raise OpenHandsPatchError("Patch paths must use POSIX-style '/' separators")
        parsed = PurePosixPath(path)
        first_part = parsed.parts[0] if parsed.parts else ""
        if parsed.is_absolute() or ".." in parsed.parts or ":" in first_part:
            raise OpenHandsPatchError(
                f"Patch path must stay relative to the repository workspace: {path}"
            )