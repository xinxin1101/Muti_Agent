from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import workspace


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> workspace.LocalGitWorkspace:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "devflow-tests@example.com")
    _git(root, "config", "user.name", "DevFlow Tests")
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return workspace.LocalGitWorkspace(root)


def test_manager_freezes_base_commit_and_creates_locked_clean_task_worktree(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    expected_base = _git(base.root, "rev-parse", "HEAD")
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    record = manager.create("TASK-001")

    assert manager.base_commit == expected_base
    assert record.base_commit == expected_base
    assert record.path.is_dir()
    assert _git(record.path, "rev-parse", "HEAD") == expected_base
    assert _git(record.path, "symbolic-ref", "--short", "HEAD") == record.branch_name
    assert manager.open_workspace("TASK-001").changed_files() == []
    porcelain = _git(base.root, "worktree", "list", "--porcelain")
    assert f"worktree {record.path.as_posix()}" in porcelain
    assert f"branch refs/heads/{record.branch_name}" in porcelain
    assert "locked DevFlow task TASK-001" in porcelain


def test_two_task_worktrees_do_not_observe_each_others_uncommitted_changes(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    task_a = manager.create("TASK-A")
    task_b = manager.create("TASK-B")

    (task_a.path / "shared.txt").write_text("task-a-only\n", encoding="utf-8")

    assert (task_a.path / "shared.txt").read_text(encoding="utf-8") == "task-a-only\n"
    assert (task_b.path / "shared.txt").read_text(encoding="utf-8") == "base\n"
    assert (base.root / "shared.txt").read_text(encoding="utf-8") == "base\n"
    assert manager.open_workspace("TASK-A").changed_files() == ["shared.txt"]
    assert manager.open_workspace("TASK-B").changed_files() == []
    assert base.changed_files() == []


def test_new_worktrees_keep_using_frozen_base_after_main_head_advances(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    frozen = manager.base_commit

    (base.root / "shared.txt").write_text("new-main\n", encoding="utf-8")
    _git(base.root, "add", "shared.txt")
    _git(base.root, "commit", "-m", "advance main")
    assert _git(base.root, "rev-parse", "HEAD") != frozen

    record = manager.create("TASK-OLD-BASE")

    assert _git(record.path, "rev-parse", "HEAD") == frozen
    assert (record.path / "shared.txt").read_text(encoding="utf-8") == "base\n"


def test_explicit_descendant_commit_can_be_used_as_a_future_task_base(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    (base.root / "dependency.txt").write_text("integrated-upstream\n", encoding="utf-8")
    _git(base.root, "add", "dependency.txt")
    _git(base.root, "commit", "-m", "integrate upstream task")
    descendant = _git(base.root, "rev-parse", "HEAD")

    record = manager.create("TASK-DOWNSTREAM", base_commit=descendant)

    assert record.base_commit == descendant
    assert _git(record.path, "rev-parse", "HEAD") == descendant
    assert (record.path / "dependency.txt").read_text(encoding="utf-8") == ("integrated-upstream\n")


def test_task_base_must_be_a_descendant_of_the_frozen_run_base(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    tree = _git(base.root, "rev-parse", "HEAD^{tree}")
    unrelated = _git(base.root, "commit-tree", tree, "-m", "unrelated root")

    with pytest.raises(workspace.TaskWorktreeError, match="must descend"):
        manager.create("TASK-UNRELATED", base_commit=unrelated)


def test_task_base_requires_a_full_immutable_commit_id(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    with pytest.raises(ValueError, match="full 40-64 character hexadecimal"):
        manager.create("TASK-SHORT-SHA", base_commit=manager.base_commit[:12])


def test_duplicate_task_creation_fails_closed_without_reusing_branch_or_path(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    first = manager.create("TASK-001")

    with pytest.raises(workspace.TaskWorktreeCollisionError, match="already registered"):
        manager.create("TASK-001")

    assert first.path.is_dir()
    assert _git(first.path, "symbolic-ref", "--short", "HEAD") == first.branch_name


def test_unregistered_existing_task_path_is_treated_as_collision(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    record = manager.record_for("TASK-001")
    record.path.mkdir()

    with pytest.raises(workspace.TaskWorktreeCollisionError, match="not registered"):
        manager.create("TASK-001")


def test_missing_locked_worktree_directory_is_reported_as_stale_registration(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    record = manager.create("TASK-STALE")
    shutil.rmtree(record.path)

    with pytest.raises(workspace.StaleTaskWorktreeError, match="stale Git worktree"):
        manager.create("TASK-STALE")
    with pytest.raises(workspace.StaleTaskWorktreeError, match="missing"):
        manager.open_workspace("TASK-STALE")


def test_manager_rejects_dirty_base_before_freezing_commit(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    (base.root / "shared.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(workspace.TaskWorktreeError, match="base workspace must be clean"):
        workspace.TaskWorktreeManager(base, tmp_path / "worktrees")


def test_create_rejects_base_that_became_dirty_after_manager_initialization(
    tmp_path: Path,
) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    (base.root / "shared.txt").write_text("dirty-later\n", encoding="utf-8")

    with pytest.raises(workspace.TaskWorktreeError, match="became dirty"):
        manager.create("TASK-001")


def test_worktree_root_must_not_live_inside_or_pollute_base_repository(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    forbidden_root = base.root / ".devflow" / "worktrees"

    with pytest.raises(ValueError, match="outside the base repository"):
        workspace.TaskWorktreeManager(base, forbidden_root)

    assert not (base.root / ".devflow").exists()
    assert base.changed_files() == []


def test_clean_remove_deletes_linked_worktree_but_preserves_task_branch(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    record = manager.create("TASK-CLEAN")

    assert manager.remove("TASK-CLEAN")

    assert not record.path.exists()
    assert manager.remove("TASK-CLEAN") is False
    branch_check = subprocess.run(
        [
            "git",
            "-C",
            str(base.root),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{record.branch_name}",
        ],
        check=False,
    )
    assert branch_check.returncode == 0


def test_clean_remove_preserves_committed_task_output_on_branch(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    record = manager.create("TASK-COMMITTED")
    (record.path / "shared.txt").write_text("committed-task-output\n", encoding="utf-8")
    _git(record.path, "add", "shared.txt")
    _git(record.path, "commit", "-m", "task output")
    task_head = _git(record.path, "rev-parse", "HEAD")

    assert manager.remove("TASK-COMMITTED")

    assert not record.path.exists()
    assert _git(base.root, "rev-parse", record.branch_name) == task_head


def test_dirty_remove_requires_explicit_force_and_still_preserves_branch(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")
    record = manager.create("TASK-DIRTY")
    (record.path / "shared.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(workspace.TaskWorktreeError, match="without force=True"):
        manager.remove("TASK-DIRTY")
    assert record.path.exists()

    assert manager.remove("TASK-DIRTY", force=True)
    assert not record.path.exists()
    assert _git(base.root, "show-ref", "--verify", f"refs/heads/{record.branch_name}")


def test_task_id_is_not_used_as_a_raw_branch_name(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    record = manager.create("TASK..lock.")

    assert ".." not in record.branch_name
    assert not record.branch_name.endswith(".")
    assert _git(record.path, "symbolic-ref", "--short", "HEAD") == record.branch_name


def test_manager_rejects_task_ids_that_do_not_match_task_contract_format(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    with pytest.raises(ValueError, match="TaskContract task_id format"):
        manager.record_for(" TASK-001")


def test_similar_task_ids_receive_distinct_paths_and_branches(tmp_path: Path) -> None:
    base = _repository(tmp_path)
    manager = workspace.TaskWorktreeManager(base, tmp_path / "worktrees")

    dotted = manager.record_for("TASK.A")
    dashed = manager.record_for("TASK-A")

    assert dotted.path != dashed.path
    assert dotted.branch_name != dashed.branch_name
