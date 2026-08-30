from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.models.failure import FailureReport, FailureSource, FailureType
from app.models.task import TaskContract
from app.models.verification import CheckResult, CheckType, VerificationResult
from app.verification.sandbox import DockerSandboxRunner, VerificationCommandRunner
from app.workspace import LocalGitWorkspace, ScopeCheckResult, ScopeEnforcer

_MAX_OUTPUT_CHARS = 20_000
_PYTHON_OUTPUT_ASSERTION = re.compile(
    r"^test\s+['\"]\$\((python(?:3)?\s+[^()]+)\)['\"]\s*=\s*['\"]([^'\"]*)['\"]$"
)


@dataclass(frozen=True)
class _CommandSpec:
    argv: list[str]
    check_type: CheckType
    failure_type: FailureType
    name: str
    requires_sandbox: bool = False
    expected_stdout: str | None = None


@dataclass(frozen=True)
class _PreparedCommand:
    command: str
    spec: _CommandSpec
    stage: str


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

        prepared: list[_PreparedCommand] = []
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

            prepared.append(
                _PreparedCommand(
                    command=command,
                    spec=spec,
                    stage=self._verification_stage(spec),
                )
            )

        # Targeted checks expose an inexpensive defect before broad suite/lint checks.  Keeping
        # this ordering deterministic also makes a failed repair cheaper: the broad stage is not
        # started after a fast-stage failure and the next attempt receives the focused evidence.
        for item in sorted(prepared, key=lambda value: value.stage != "fast"):
            command = item.command
            spec = item.spec
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

            command_check = self._run_command(command, spec, workspace=workspace)
            check = command_check.model_copy(
                update={
                    "execution_details": (
                        *command_check.execution_details,
                        f"verification_stage={item.stage}",
                    )
                }
            )
            checks.append(check)
            if item.stage == "fast" and not check.passed:
                break

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
        stderr = execution.stderr
        if passed and spec.expected_stdout is not None:
            actual = execution.stdout.rstrip("\r\n")
            passed = actual == spec.expected_stdout
            if not passed:
                stderr = (
                    "Program stdout did not match the expected value. "
                    f"expected={spec.expected_stdout!r}; actual={actual!r}"
                )
        failure_type = None if passed else execution.failure_type or spec.failure_type
        if (
            failure_type is FailureType.TEST_FAILURE
            and spec.check_type is CheckType.CUSTOM
            and self._is_sandbox_enforcement_failure(execution.stderr)
        ):
            # A project command that attempts a forbidden write has not exposed a code defect
            # for the Repair Agent to fix. Keep it fail-closed as a sandbox/tool failure.
            failure_type = FailureType.TOOL_FAILURE
        return CheckResult(
            check_type=spec.check_type,
            name=spec.name,
            command=command,
            passed=passed,
            exit_code=execution.exit_code,
            stdout=self._clip_output(execution.stdout),
            stderr=self._clip_output(stderr),
            duration_ms=execution.duration_ms,
            failure_type=failure_type,
            execution_backend=execution.backend,
            execution_details=execution.details,
        )

    @staticmethod
    def _classify_command(command: str) -> _CommandSpec:
        output_assertion = _PYTHON_OUTPUT_ASSERTION.fullmatch(command.strip())
        if output_assertion is not None:
            inner_command, expected_stdout = output_assertion.groups()
            argv = shlex.split(inner_command, posix=True)
            DeterministicVerifier._assert_workspace_bound_arguments(argv[1:])
            return _CommandSpec(
                argv=argv,
                check_type=CheckType.TEST,
                failure_type=FailureType.TEST_FAILURE,
                name="python_stdout",
                requires_sandbox=True,
                expected_stdout=expected_stdout,
            )
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
            # A custom command has already passed command-boundary validation and runs inside
            # the read-only Docker sandbox. A normal non-zero exit is therefore an executable
            # acceptance-test failure, which the Repair Agent can address using its evidence.
            failure_type=FailureType.TEST_FAILURE,
            name="custom",
            requires_sandbox=True,
        )

    @staticmethod
    def _verification_stage(spec: _CommandSpec) -> str:
        """Classify only by already-validated argv; no heuristic model decision is involved."""

        if spec.check_type is CheckType.CUSTOM or spec.expected_stdout is not None:
            return "fast"
        if spec.check_type is CheckType.TEST or spec.check_type is CheckType.LINT:
            arguments = spec.argv[5:]
        else:
            return "full"
        has_target = any(
            argument and not argument.startswith("-") and argument != "." for argument in arguments
        )
        return "fast" if has_target else "full"

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
            if check.name == "custom":
                return "Deterministic custom verification failed."
            return "Deterministic pytest verification failed."
        if check.failure_type is FailureType.LINT_FAILURE:
            return "Deterministic Ruff verification failed."
        if check.failure_type is FailureType.SANDBOX_TIMEOUT:
            return "Docker verification sandbox exceeded its execution deadline."
        if check.failure_type is FailureType.VERIFICATION_ENV_UNAVAILABLE:
            if "验证环境缺少 Node.js" in check.stderr:
                return "验证环境缺少 Node.js，无法执行当前 JavaScript 验证命令。"
            if "验证环境缺少 Python" in check.stderr:
                return "验证环境缺少 Python，无法执行当前验证命令。"
            return "验证环境不可用，无法安全执行当前验证命令。"
        return "Deterministic verification could not complete safely."

    @staticmethod
    def _clip_output(value: str | None) -> str:
        # A Windows subprocess decode failure can leave ``CompletedProcess.stdout`` unset even
        # though the command itself completed. The sandbox normalizes this at the boundary; keep
        # the verifier defensive so diagnostic projection never masks the original command error.
        if value is None:
            return ""
        if len(value) <= _MAX_OUTPUT_CHARS:
            return value
        return value[:_MAX_OUTPUT_CHARS] + "\n...[truncated by DevFlow]"

    @staticmethod
    def _is_sandbox_enforcement_failure(stderr: str) -> bool:
        normalized = stderr.lower()
        return "read-only file system" in normalized
