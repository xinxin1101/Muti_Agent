from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.workspace import (
    CommitDiffError,
    CommitDiffFileStatus,
    CommitDiffOmissionReason,
    LocalGitWorkspace,
    ReadOnlyCommitDiffReader,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> tuple[LocalGitWorkspace, str, str]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "DevFlow Test")
    _git(root, "config", "user.email", "devflow@example.com")
    (root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (root / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    base = _commit(root, "base")

    (root / "alpha.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (root / "added.txt").write_text("new file\n", encoding="utf-8")
    (root / "deleted.txt").unlink()
    (root / "binary.bin").write_bytes(b"\x00\x01\x02\xff")
    head = _commit(root, "head")
    return LocalGitWorkspace(root), base, head


def test_reader_returns_bounded_commit_diff_without_mutating_repository(tmp_path: Path) -> None:
    workspace, base, head = _repository(tmp_path)
    reader = ReadOnlyCommitDiffReader(workspace)
    before_head = workspace.head_commit()
    before_changes = workspace.changed_files()

    result = reader.read(base_commit=base, head_commit=head)

    assert result.base_commit == base
    assert result.head_commit == head
    assert result.changed_file_count == 4
    assert result.omitted_file_count == 0
    assert result.additions >= 2
    assert result.deletions >= 1
    files = {item.path: item for item in result.files}
    assert files["alpha.txt"].status is CommitDiffFileStatus.MODIFIED
    assert "+beta" in (files["alpha.txt"].patch or "")
    assert files["added.txt"].status is CommitDiffFileStatus.ADDED
    assert files["deleted.txt"].status is CommitDiffFileStatus.DELETED
    assert files["binary.bin"].binary is True
    assert files["binary.bin"].patch is None
    assert files["binary.bin"].patch_omitted_reason is CommitDiffOmissionReason.BINARY
    assert workspace.head_commit() == before_head
    assert workspace.changed_files() == before_changes == []


def test_reader_enforces_file_patch_and_blob_bounds(tmp_path: Path) -> None:
    workspace, base, head = _repository(tmp_path)
    reader = ReadOnlyCommitDiffReader(
        workspace,
        max_files=1,
        max_file_patch_bytes=24,
        max_total_patch_bytes=24,
        max_blob_bytes=8,
    )

    result = reader.read(base_commit=base, head_commit=head)

    assert result.changed_file_count == 4
    assert len(result.files) == 1
    assert result.omitted_file_count == 3
    assert result.truncated is True
    assert result.patch_bytes <= 24
    assert result.files[0].patch_truncated is True


def test_reader_rejects_untrusted_or_missing_commit_evidence(tmp_path: Path) -> None:
    workspace, base, _head = _repository(tmp_path)
    reader = ReadOnlyCommitDiffReader(workspace)

    with pytest.raises(CommitDiffError, match="full lowercase"):
        reader.read(base_commit="HEAD", head_commit=base)
    with pytest.raises(CommitDiffError, match="does not resolve"):
        reader.read(base_commit=base, head_commit="f" * 40)
