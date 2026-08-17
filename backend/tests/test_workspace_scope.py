import subprocess
from pathlib import Path

import pytest

from app.models import FailureType, TaskContract
from app.workspace import LocalGitWorkspace, ScopeEnforcer, ScopeViolationKind, WorkspaceGitError


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _make_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app" / "nested").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()

    (root / "app" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "app" / "nested" / "worker.py").write_text("WORKER = 1\n", encoding="utf-8")
    (root / "tests" / "test_main.py").write_text("def test_value(): pass\n", encoding="utf-8")
    (root / "docs" / "notes.md").write_text("baseline\n", encoding="utf-8")

    _git(root.parent, "init", str(root))
    _git(root, "config", "user.email", "devflow@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def _task(
    *,
    writable_files: list[str] | None = None,
    readonly_files: list[str] | None = None,
) -> TaskContract:
    return TaskContract(
        task_id="SCOPE-001",
        objective="Modify the implementation without touching protected files.",
        readable_files=["app/**", "tests/**"],
        writable_files=writable_files or ["app/**"],
        readonly_files=readonly_files or ["tests/**"],
        acceptance_criteria=["Implementation change is present."],
        verification_commands=["pytest -q"],
    )


def test_workspace_requires_existing_git_repository_with_head(tmp_path: Path) -> None:
    plain_directory = tmp_path / "plain"
    plain_directory.mkdir()

    with pytest.raises(WorkspaceGitError, match="Git working tree"):
        LocalGitWorkspace(plain_directory)

    empty_repo = tmp_path / "empty-repo"
    empty_repo.mkdir()
    _git(empty_repo.parent, "init", str(empty_repo))

    with pytest.raises(WorkspaceGitError, match="valid HEAD"):
        LocalGitWorkspace(empty_repo)


def test_workspace_root_must_be_git_top_level(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)

    with pytest.raises(WorkspaceGitError, match="top level"):
        LocalGitWorkspace(root / "app")


def test_changed_files_collects_tracked_staged_and_untracked_changes(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)

    (root / "app" / "main.py").write_text("VALUE = 2\n", encoding="utf-8")
    (root / "app" / "new.py").write_text("NEW = True\n", encoding="utf-8")
    (root / "docs" / "notes.md").write_text("staged\n", encoding="utf-8")
    _git(root, "add", "docs/notes.md")

    assert workspace.changed_files() == ["app/main.py", "app/new.py", "docs/notes.md"]


def test_allowed_source_modification_passes_scope_gate(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    enforcer = ScopeEnforcer()

    (root / "app" / "nested" / "worker.py").write_text("WORKER = 2\n", encoding="utf-8")

    result = enforcer.check(_task(), workspace.changed_files())

    assert result.passed is True
    assert result.allowed_files == ["app/nested/worker.py"]
    assert result.violations == []
    assert result.to_failure_report() is None


def test_test_file_tampering_triggers_scope_violation(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    enforcer = ScopeEnforcer()

    protected_test = root / "tests" / "test_main.py"
    protected_test.write_text("def test_value(): assert True\n", encoding="utf-8")

    result = enforcer.check(_task(), workspace.changed_files())
    failure = result.to_failure_report()

    assert result.passed is False
    assert result.violations[0].kind is ScopeViolationKind.READ_ONLY
    assert result.violations[0].matched_pattern == "tests/**"
    assert failure is not None
    assert failure.failure_type is FailureType.SCOPE_VIOLATION
    assert failure.retryable is False


def test_out_of_scope_file_cannot_silently_proceed(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    enforcer = ScopeEnforcer()

    (root / "docs" / "notes.md").write_text("unauthorized\n", encoding="utf-8")

    result = enforcer.check(_task(), workspace.changed_files())

    assert result.passed is False
    assert result.allowed_files == []
    assert result.violations[0].path == "docs/notes.md"
    assert result.violations[0].kind is ScopeViolationKind.OUT_OF_SCOPE


def test_readonly_scope_wins_over_broad_writable_scope(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    enforcer = ScopeEnforcer()
    task = _task(
        writable_files=["**"],
        readonly_files=["tests/**"],
    )

    protected_test = root / "tests" / "test_main.py"
    protected_test.write_text("def test_value(): assert False\n", encoding="utf-8")

    result = enforcer.check(task, workspace.changed_files())

    assert result.passed is False
    assert result.violations[0].kind is ScopeViolationKind.READ_ONLY


def test_glob_matching_preserves_path_segment_boundaries() -> None:
    enforcer = ScopeEnforcer()

    assert enforcer.matches("app/main.py", "app/*.py") is True
    assert enforcer.matches("app/nested/worker.py", "app/*.py") is False
    assert enforcer.matches("app/nested/worker.py", "app/**") is True
    assert enforcer.matches("main.py", "**/*.py") is True
    assert enforcer.matches("app/main.py", "**/*.py") is True


def test_renaming_protected_file_cannot_bypass_readonly_gate(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    workspace = LocalGitWorkspace(root)
    enforcer = ScopeEnforcer()

    _git(root, "mv", "tests/test_main.py", "app/test_main.py")

    changed = workspace.changed_files()
    result = enforcer.check(_task(), changed)

    assert "tests/test_main.py" in changed
    assert "app/test_main.py" in changed
    assert result.passed is False
    assert any(
        violation.path == "tests/test_main.py" and violation.kind is ScopeViolationKind.READ_ONLY
        for violation in result.violations
    )


def test_resolve_path_blocks_traversal_absolute_and_backslash_paths(tmp_path: Path) -> None:
    workspace = LocalGitWorkspace(_make_repository(tmp_path))

    assert workspace.resolve_path("app/main.py") == workspace.root / "app" / "main.py"

    with pytest.raises(ValueError, match="inside the workspace"):
        workspace.resolve_path("../outside.txt")
    with pytest.raises(ValueError, match="relative"):
        workspace.resolve_path("/tmp/outside.txt")
    with pytest.raises(ValueError, match="POSIX-style"):
        workspace.resolve_path("app\\main.py")


def test_resolve_path_blocks_existing_symlink_escape(tmp_path: Path) -> None:
    root = _make_repository(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    workspace = LocalGitWorkspace(root)

    with pytest.raises(ValueError, match="resolves outside"):
        workspace.resolve_path("escape/payload.py")
