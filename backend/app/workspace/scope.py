from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.task import TaskContract


class ScopeViolationKind(StrEnum):
    READ_ONLY = "READ_ONLY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class ScopeViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    kind: ScopeViolationKind
    matched_pattern: str | None = None


class ScopeCheckResult(BaseModel):
    """Deterministic repository-scope evidence produced before verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changed_files: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)
    violations: list[ScopeViolation] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_failure_report(self) -> FailureReport | None:
        if self.passed:
            return None

        evidence = [
            f"{violation.kind.value}:{violation.path}"
            + (
                f":matched={violation.matched_pattern}"
                if violation.matched_pattern is not None
                else ""
            )
            for violation in self.violations
        ]
        return FailureReport(
            failure_type=FailureType.SCOPE_VIOLATION,
            source=FailureSource.RUNTIME,
            message="Repository changes violated the task write-scope contract.",
            retryable=False,
            evidence=evidence,
        )


class ScopeEnforcer:
    """Compare actual Git changes with one TaskContract's write boundary."""

    def check(self, task: TaskContract, changed_files: list[str]) -> ScopeCheckResult:
        normalized_changed = sorted({self._normalize_changed_path(path) for path in changed_files})
        allowed: list[str] = []
        violations: list[ScopeViolation] = []

        for path in normalized_changed:
            readonly_pattern = self._first_match(path, task.readonly_files)
            if readonly_pattern is not None:
                violations.append(
                    ScopeViolation(
                        path=path,
                        kind=ScopeViolationKind.READ_ONLY,
                        matched_pattern=readonly_pattern,
                    )
                )
                continue

            writable_pattern = self._first_match(path, task.writable_files)
            if writable_pattern is None:
                violations.append(
                    ScopeViolation(
                        path=path,
                        kind=ScopeViolationKind.OUT_OF_SCOPE,
                    )
                )
                continue

            allowed.append(path)

        return ScopeCheckResult(
            changed_files=normalized_changed,
            allowed_files=allowed,
            violations=violations,
        )

    @classmethod
    def matches(cls, path: str, pattern: str) -> bool:
        return re.fullmatch(cls._glob_regex(pattern), path) is not None

    @classmethod
    def _first_match(cls, path: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            if cls.matches(path, pattern):
                return pattern
        return None

    @staticmethod
    def _normalize_changed_path(path: str) -> str:
        normalized = path.strip()
        if not normalized:
            raise ValueError("changed path must not be empty")
        if normalized.startswith(("/", "\\")) or "\\" in normalized:
            raise ValueError("changed path must be repository-relative POSIX style")
        if any(part == ".." for part in normalized.split("/")):
            raise ValueError("changed path must not traverse outside the repository")
        return normalized

    @staticmethod
    def _glob_regex(pattern: str) -> str:
        """Translate V0.1 repository globs while preserving '/' boundaries.

        Supported wildcards:
        - `*` matches within one path segment.
        - `?` matches one non-separator character.
        - `**` matches across path segments.
        - `**/` also matches zero directory levels.
        """

        result: list[str] = []
        index = 0
        while index < len(pattern):
            if pattern.startswith("**/", index):
                result.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                result.append(".*")
                index += 2
                continue

            char = pattern[index]
            if char == "*":
                result.append("[^/]*")
            elif char == "?":
                result.append("[^/]")
            else:
                result.append(re.escape(char))
            index += 1

        return "".join(result)
