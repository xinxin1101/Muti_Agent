from __future__ import annotations

import shlex
from dataclasses import dataclass

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.task import TaskContract
from app.models.verification import CheckResult, CheckType, VerificationResult
from app.verification.sandbox import DockerSandboxRunner, VerificationCommandRunner
from app.workspace import LocalGitWorkspace, ScopeCheckResult, ScopeEnforcer

_MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class _CommandSpec:
    argv: list[str]
    check_type: CheckType
    failure_type: FailureType
    name: str
    requires_sandbox: bool = False


class DeterministicVerifier:
    """Run deterministic hard-gate checks against repository state.

    Scope integrity is checked before any target-project process starts. The default runner is the
    Docker verification sandbox. Known pytest/Ruff commands retain typed failure semantics, while
    project-specific commands are accepted only when the selected runner is sandboxed.
    """

    def __init__(
        self,
        *,
        command_timeout_seconds: float = 60.0,
        max_commands: int = 8,
        scope_enforcer: ScopeEnforcer | None = None,
        command_runner: VerificationCommandRunner | None = None,
    ) -> None:
        if not 0.05 <= command_timeout_seconds <= 600.0:
            raise ValueError("command_timeout_seconds must be between 0.05 and 600")
        if not 1 <= max_commands <= 32:
            raise ValueError("max_commands must be between 1 and 32")
        self._command_timeout_seconds = command_timeout_seconds
        self._max_commands = max_commands
        self._scope_enforcer = scope_enforcer or ScopeEnforcer()
        self._command_runner = command_runner or DockerSandboxRunner()

    @property
    def command_runner(self) -> VerificationCommandRunner:
        return self._command_runner

    def verify(
        self,
        task: TaskContract,
        *,
        workspace: LocalGitWorkspace,
    ) -> VerificationResult:
        checks: list[CheckResult] = []
        scope_result = self._scope_enforcer.check(task, workspace.changed_files())
        checks.append(self._scope_check("git_scope", scope_result))
        if not scope_result.passed:
            return VerificationResult(passed=False, checks=checks)

        if len(task.verification_commands) > self._max_commands:
            checks.append(
                CheckResult(
                    check_type=CheckType.CUSTOM,
                    name="verification_command_budget",
                    passed=False,
                    stderr=(
                        "Task requested more verification commands than the runtime budget: "
                        f"{len(task.verification_commands)} > {self._max_commands}."
                    ),
                    failure_type=FailureType.TOOL_FAILURE,
                )
            )
            return VerificationResult(passed=False, checks=checks)

        for command in task.verification_commands:
            try:
                spec = self._classify_command(command)
            except ValueError as exc:
                checks.append(
                    CheckResult(
                        check_type=CheckType.CUSTOM,
                        name="unsupported_verification_command",
                        command=command,
                        passed=False,
                        stderr=str(exc),
                        failure_type=FailureType.TOOL_FAILURE,
                    )
                )
                continue

            if spec.requires_sandbox and not self._command_runner.is_sandboxed:
                checks.append(
                    CheckResult(
                        check_type=CheckType.CUSTOM,
                        name="sandbox_required",
                        command=command,
                        passed=False,
                        stderr=(
                            "Project-specific verification commands require the Docker sandbox; "
                            "host execution is refused."
                        ),
                        failure_type=FailureType.TOOL_FAILURE,
                    )
                )
                continue

            checks.append(self._run_command(command, spec, workspace=workspace))

        post_scope = self._scope_enforcer.check(task, workspace.changed_files())
        if not post_scope.passed:
            checks.append(self._scope_check("git_scope_post_verification", post_scope))

        return VerificationResult(
            passed=all(check.passed for check in checks),
            checks=checks,
        )

    @staticmethod
    def failure_reports(result: VerificationResult) -> list[FailureReport]:
        reports: list[FailureReport] = []
        for check in result.checks:
            if check.passed or check.failure_type is None:
                continue
            evidence = [f"check={check.name}"]
            if check.command:
                evidence.append(f"command={check.command}")
            if check.exit_code is not None:
                evidence.append(f"exit_code={check.exit_code}")
            if check.execution_backend is not None:
                evidence.append(f"execution_backend={check.execution_backend.value}")
            evidence.extend(f"execution={detail}" for detail in check.execution_details)
            if check.stderr:
                evidence.append(f"stderr={check.stderr}")
            elif check.stdout:
                evidence.append(f"stdout={check.stdout}")
            reports.append(
                FailureReport(
                    failure_type=check.failure_type,
                    source=FailureSource.VERIFICATION,
                    message=DeterministicVerifier._failure_message(check),
                    retryable=check.failure_type
                    in {FailureType.TEST_FAILURE, FailureType.LINT_FAILURE},
                    evidence=evidence,
                )
            )
        return reports

    def _run_command(
        self,
        command: str,
        spec: _CommandSpec,
        *,
        workspace: LocalGitWorkspace,
    ) -> CheckResult:
        execution = self._command_runner.run(
            spec.argv,
            workspace=workspace.root,
            timeout_seconds=self._command_timeout_seconds,
        )
        passed = execution.failure_type is None and execution.exit_code == 0
        failure_type = None if passed else execution.failure_type or spec.failure_type
        return CheckResult(
            check_type=spec.check_type,
            name=spec.name,
            command=command,
            passed=passed,
            exit_code=execution.exit_code,
            stdout=self._clip_output(execution.stdout),
            stderr=self._clip_output(execution.stderr),
            duration_ms=execution.duration_ms,
            failure_type=failure_type,
            execution_backend=execution.backend,
            execution_details=execution.details,
        )

    @staticmethod
    def _classify_command(command: str) -> _CommandSpec:
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ValueError(f"Invalid verification command syntax: {exc}") from exc
        if not argv:
            raise ValueError("Verification command must not be empty.")
        if argv[0].startswith("-"):
            raise ValueError("Verification executable must not begin with an option prefix.")

        executable = argv[0]
        if executable in {"pytest", "py.test"}:
            arguments = argv[1:]
            DeterministicVerifier._assert_workspace_bound_arguments(arguments)
            return _CommandSpec(
                argv=["python", "-m", "pytest", "-p", "no:cacheprovider", *arguments],
                check_type=CheckType.TEST,
                failure_type=FailureType.TEST_FAILURE,
                name="pytest",
            )

        if executable == "ruff" and len(argv) >= 2 and argv[1] == "check":
            arguments = argv[1:]
            DeterministicVerifier._assert_workspace_bound_arguments(arguments)
            DeterministicVerifier._assert_non_mutating_ruff(arguments)
            return _CommandSpec(
                argv=["python", "-m", "ruff", "check", "--no-cache", *arguments[1:]],
                check_type=CheckType.LINT,
                failure_type=FailureType.LINT_FAILURE,
                name="ruff",
            )

        if executable in {"python", "python3"} and len(argv) >= 3 and argv[1] == "-m":
            module = argv[2]
            if module == "pytest":
                arguments = argv[3:]
                DeterministicVerifier._assert_workspace_bound_arguments(arguments)
                return _CommandSpec(
                    argv=["python", "-m", "pytest", "-p", "no:cacheprovider", *arguments],
                    check_type=CheckType.TEST,
                    failure_type=FailureType.TEST_FAILURE,
                    name="pytest",
                )
            if module == "ruff" and len(argv) >= 4 and argv[3] == "check":
                arguments = argv[3:]
                DeterministicVerifier._assert_workspace_bound_arguments(arguments)
                DeterministicVerifier._assert_non_mutating_ruff(arguments)
                return _CommandSpec(
                    argv=["python", "-m", "ruff", "check", "--no-cache", *arguments[1:]],
                    check_type=CheckType.LINT,
                    failure_type=FailureType.LINT_FAILURE,
                    name="ruff",
                )

        DeterministicVerifier._assert_workspace_bound_arguments(argv[1:])
        return _CommandSpec(
            argv=argv,
            check_type=CheckType.CUSTOM,
            failure_type=FailureType.TOOL_FAILURE,
            name="custom",
            requires_sandbox=True,
        )

    @staticmethod
    def _assert_workspace_bound_arguments(arguments: list[str]) -> None:
        for argument in arguments:
            candidates = [argument]
            if "=" in argument:
                candidates.append(argument.split("=", 1)[1])
            for candidate in candidates:
                normalized = candidate.strip().replace("\\", "/")
                if not normalized:
                    continue
                if normalized.startswith("/"):
                    raise ValueError("Verification command arguments must remain inside workspace.")
                if len(normalized) >= 2 and normalized[1] == ":":
                    raise ValueError("Verification command arguments must not use drive paths.")
                if any(part == ".." for part in normalized.split("/")):
                    raise ValueError("Verification command arguments must not traverse workspace.")

    @staticmethod
    def _assert_non_mutating_ruff(arguments: list[str]) -> None:
        for argument in arguments:
            if argument in {"--fix", "--fix-only"}:
                raise ValueError("Verification Ruff commands must not mutate the workspace.")
            if argument.startswith(("--fix=", "--fix-only=")):
                raise ValueError("Verification Ruff commands must not mutate the workspace.")

    @staticmethod
    def _scope_check(name: str, scope_result: ScopeCheckResult) -> CheckResult:
        return CheckResult(
            check_type=CheckType.SCOPE,
            name=name,
            passed=scope_result.passed,
            failure_type=None if scope_result.passed else FailureType.SCOPE_VIOLATION,
            stderr=DeterministicVerifier._scope_error(scope_result),
        )

    @staticmethod
    def _scope_error(scope_result: ScopeCheckResult) -> str:
        if scope_result.passed:
            return ""
        return "; ".join(
            f"{violation.kind.value}:{violation.path}"
            + (
                f":matched={violation.matched_pattern}"
                if violation.matched_pattern is not None
                else ""
            )
            for violation in scope_result.violations
        )

    @staticmethod
    def _failure_message(check: CheckResult) -> str:
        if check.failure_type is FailureType.SCOPE_VIOLATION:
            if check.name == "git_scope_post_verification":
                return "Verification commands produced out-of-scope repository changes."
            return "Git scope integrity check failed before deterministic verification."
        if check.failure_type is FailureType.TEST_FAILURE:
            return "Deterministic pytest verification failed."
        if check.failure_type is FailureType.LINT_FAILURE:
            return "Deterministic Ruff verification failed."
        if check.failure_type is FailureType.SANDBOX_TIMEOUT:
            return "Docker verification sandbox exceeded its execution deadline."
        return "Deterministic verification could not complete safely."

    @staticmethod
    def _clip_output(value: str) -> str:
        if len(value) <= _MAX_OUTPUT_CHARS:
            return value
        return value[:_MAX_OUTPUT_CHARS] + "\n...[truncated by DevFlow]"
